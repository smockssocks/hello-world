"""The engine ties everything together: controller, haptics, game integrations and profiles."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from . import protocol as p
from .controller import BLUETOOTH, USB, Controller
from .dsx_server import DsxServer, TextFileWatcher, find_mod_text_file
from .haptics.audio import AudioCapture
from .haptics.outputs import BluetoothHapticsOutput, HapticOutput, RumbleOutput, UsbAudioOutput
from .haptics.synth import HapticMixer
from .integrations import REGISTRY, Integration, Outputs
from .layers import AudioLayer, XboxBridge
from .profiles import DEFAULT_PROFILE_NAME, Profile, find_process_exe, load_profiles, running_processes
from .settings import Settings
from .triggers import OFF, TriggerEffect

log = logging.getLogger(__name__)

TICK_HZ = 100
TRIGGER_UPDATE_INTERVAL = 1 / 30
PROFILE_SCAN_INTERVAL = 2.0


class Engine:
    def __init__(self, settings: Settings | None = None, controller: Controller | None = None,
                 capture: AudioCapture | None = None):
        self.settings = settings or Settings.load()
        self.controller = controller or Controller()
        self.mixer = HapticMixer()
        self.capture = capture or AudioCapture()
        self.audio_layer = AudioLayer(self.capture)
        self.xbox = XboxBridge(self.mixer)
        self.dsx = DsxServer(status_fn=lambda: (self.controller.connected, self.controller.input.battery,
                                                self.controller.transport))
        self.profiles = load_profiles()
        self.profile: Profile = self.profiles[DEFAULT_PROFILE_NAME]
        self.detected_game: str | None = None
        self.integration: Integration | None = None
        self.output: HapticOutput | None = None
        self.output_note = ""
        self.lab_override: tuple[TriggerEffect | None, TriggerEffect | None] | None = None
        self.messages: deque[str] = deque(maxlen=50)
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._reconfigure_output = False
        self._profile_applied = False
        self._last_trigger_update = 0.0
        self._last_scan = 0.0
        self._output_key = None
        self._released = False
        self.mod_text_file = None
        self._text_watcher: TextFileWatcher | None = None
        self._text_search_at = 0.0
        self._text_searching = False
        self.last_outputs = Outputs()
        self.controller.on_connection(self._on_connection)
        self.controller.on_input(self._on_input)

    # ------------------------------------------------------------- lifecycle
    @property
    def input(self) -> p.InputState:
        return self.controller.input

    def start(self) -> None:
        self._running = True
        self.controller.start()
        if self.settings.audio_capture:
            self.start_audio_capture()
        if self.settings.dsx_server:
            self.start_dsx_server()
        self._apply_profile(self.profiles.get(self.settings.profile, self.profile))
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        with self._lock:
            if self.integration:
                self.integration.stop(self)
            self._stop_output()
            self.xbox.stop()
            self.capture.stop()
            self.dsx.stop()
        self.controller.stop()

    def note(self, msg: str) -> None:
        log.info(msg)
        self.messages.append(time.strftime("%H:%M:%S ") + msg)

    def start_dsx_server(self) -> None:
        if not self.dsx.start():
            self.note("Mod server unavailable: %s" % self.dsx.error)

    def start_audio_capture(self) -> None:
        if self.capture.running:
            return
        if not self.capture.start():
            self.note("Audio haptics unavailable: %s" % self.capture.error)

    # --------------------------------------------------------------- profiles
    def reload_profiles(self) -> None:
        with self._lock:
            self.profiles = load_profiles()
            current = self.profiles.get(self.profile.name, self.profiles[DEFAULT_PROFILE_NAME])
            self._apply_profile(current, force=True)

    def select_profile(self, name: str) -> None:
        """Manual choice; used when no known game is running."""
        self.settings.profile = name
        self.settings.save()
        if name in self.profiles and (self.detected_game is None or not self.settings.auto_profiles):
            self._apply_profile(self.profiles[name])

    def _apply_profile(self, profile: Profile, force: bool = False) -> None:
        with self._lock:
            if profile is self.profile and self._profile_applied and not force:
                return
            self._profile_applied = True
            if self.integration:
                self.integration.stop(self)
                self.integration = None
            if profile is not self.profile:
                self.dsx.reset()  # effects a mod set for the previous game shouldn't linger
                self._text_watcher, self.mod_text_file, self._text_search_at = None, None, 0.0
            self.profile = profile
            if profile.integration:
                cls = REGISTRY.get(profile.integration)
                if cls is None:
                    self.note("Unknown integration '%s' in profile %s" % (profile.integration, profile.name))
                else:
                    self.integration = cls(profile.integration_settings)
                    self.integration.start(self)
            if profile.xbox_bridge:
                if not self.xbox.start():
                    self.note(self.xbox.error)
            else:
                self.xbox.stop()
            self.note("Profile: %s" % profile.name)

    def _scan_for_games(self) -> None:
        running = running_processes()
        match = None
        # Your own profiles win over built-in ones for the same game.
        ordered = sorted(self.profiles.values(), key=lambda pr: pr.builtin)
        for prof in ordered:
            if prof.exe and prof.matches_exe(running):
                match = prof
                break
        if match is not None:
            if self.detected_game != match.name:
                self.detected_game = match.name
                self.note("Detected game: %s" % match.name)
            self._apply_profile(match)
        else:
            if self.detected_game is not None:
                self.note("Game closed")
            self.detected_game = None
            fallback = self.profiles.get(self.settings.profile, self.profiles[DEFAULT_PROFILE_NAME])
            self._apply_profile(fallback)

    # ---------------------------------------------------------------- outputs
    def _on_connection(self, connected: bool) -> None:
        self._reconfigure_output = True
        if connected:
            self.note("Controller connected (%s)" % ("Bluetooth" if self.controller.transport == BLUETOOTH else "USB"))
        else:
            self.note("Controller disconnected")

    def _on_input(self, inp: p.InputState) -> None:
        if self.xbox.running:
            self.xbox.feed(inp)

    def reconfigure_output(self) -> None:
        self._reconfigure_output = True

    def _stop_output(self) -> None:
        if self.output is not None:
            self.output.stop()
            self.output = None

    def _effective_haptics_mode(self) -> str:
        if self.settings.haptics_mode == "off" or self.profile.haptics == "off":
            return "off"
        if self.profile.haptics == "bluetooth_only" and self.controller.transport != BLUETOOTH:
            return "off"
        return self.settings.haptics_mode

    def _output_config_key(self):
        return (self.controller.connected, self.controller.transport, self._effective_haptics_mode(),
                self.settings.bt_haptics)

    def _configure_output(self) -> None:
        self._reconfigure_output = False
        self._output_key = self._output_config_key()
        self._stop_output()
        self.output_note = ""
        if not self.controller.connected:
            return
        mode = self._effective_haptics_mode()
        if mode == "off":
            self.output_note = "Haptics off"
            if self.profile.haptics == "bluetooth_only":
                self.output_note = "Off while wired - the game drives the haptics itself"
            return
        out: HapticOutput | None = None
        if mode == "auto":
            if self.controller.transport == USB:
                usb = UsbAudioOutput(self.mixer)
                if usb.start():
                    out = usb
                else:
                    self.output_note = usb.error or ""
                    self.note("USB HD haptics unavailable: %s" % usb.error)
            elif self.settings.bt_haptics:
                bt = BluetoothHapticsOutput(self.mixer, self.controller)
                bt.start()
                out = bt
        if out is None:
            out = RumbleOutput(self.mixer, self.controller)
            out.start()
        self.output = out
        self.note("Haptics: %s" % out.description)

    # ------------------------------------------------------------------- loop
    def _run(self) -> None:
        last = time.monotonic()
        period = 1.0 / TICK_HZ
        while self._running:
            now = time.monotonic()
            dt, last = now - last, now
            try:
                self._tick(now, dt)
            except Exception:
                log.exception("engine tick failed")
            time.sleep(max(0.0, period - (time.monotonic() - now)))

    def _tick(self, now: float, dt: float) -> None:
        with self._lock:
            if self._reconfigure_output or self._output_key != self._output_config_key():
                self._configure_output()
            if isinstance(self.output, BluetoothHapticsOutput) and self.output.error:
                self.note("Bluetooth HD haptics failed, falling back to rumble: %s" % self.output.error)
                self._stop_output()
                self.output = RumbleOutput(self.mixer, self.controller)
                self.output.start()
                self.output_note = "Bluetooth HD haptics not accepted by this controller/driver"
            if self.settings.auto_profiles and now - self._last_scan > PROFILE_SCAN_INTERVAL:
                self._last_scan = now
                self._scan_for_games()

            prof = self.profile
            self.mixer.gain = self.settings.haptic_intensity
            self.audio_layer.configure(self.mixer, prof.audio_haptics and self.capture.running,
                                       prof.audio_gain, prof.audio_cutoff)
            self.audio_layer.hits(self.mixer, prof.audio_haptics and prof.audio_hits, prof.audio_hit_strength)
            self._poll_mod_text_file(now)
            outputs = self.integration.tick(self, dt) if self.integration else Outputs()
            self.last_outputs = outputs
            if not self.controller.connected:
                return
            inp = self.controller.input
            mod = self.dsx.snapshot() if self.dsx.running else None
            scale = self.settings.trigger_intensity

            # Trigger priority: Trigger Lab > game mod (DSX protocol) > audio kick >
            # Xbox rumble echo > telemetry integration > profile.
            if prof.hands_off:
                left = right = None
            else:
                left, right = prof.triggers_for(inp)
                left = outputs.left_trigger or left
                right = outputs.right_trigger or right
                if prof.xbox_trigger_rumble:
                    xl, xr = self.xbox.trigger_rumble(inp)
                    left, right = xl or left, xr or right
                kl, kr = self.audio_layer.trigger_kick(inp, prof.audio_trigger_kick, prof.audio_kick_threshold)
                left, right = kl or left, kr or right
            lb = left.to_bytes(scale) if left else None
            rb = right.to_bytes(scale) if right else None
            if mod is not None:
                lb = mod.left if mod.left is not None else lb
                rb = mod.right if mod.right is not None else rb
            if self.lab_override is not None:
                ll, lr = self.lab_override
                lb = (ll or OFF).to_bytes(scale)
                rb = (lr or OFF).to_bytes(scale)
            if now - self._last_trigger_update >= TRIGGER_UPDATE_INTERVAL:
                self._last_trigger_update = now
                if prof.hands_off and lb is None and rb is None:
                    # Release our effects once, then leave the triggers to the game.
                    if not self._released:
                        self.controller.set_triggers(OFF.to_bytes(), OFF.to_bytes())
                        self._released = True
                else:
                    self._released = False
                    self.controller.set_triggers(lb or OFF.to_bytes(), rb or OFF.to_bytes())

            mod_light = mod.lightbar if mod is not None else None
            if prof.hands_off and mod_light is None:
                return
            color = mod_light or outputs.lightbar or prof.lightbar or tuple(self.settings.lightbar_color)
            self.controller.set_lightbar(*color)
            leds = mod.player_leds if mod is not None and mod.player_leds is not None else outputs.player_leds
            self.controller.set_player_leds(leds or 0)
            if mod is not None and mod.mic_led is not None:
                self.controller.set_mic_led(mod.mic_led)
            self.controller.set_led_brightness(self.settings.led_brightness)

    def _poll_mod_text_file(self, now: float) -> None:
        prof = self.profile
        if not prof.mod_text_search or self.detected_game != prof.name:
            return
        if self._text_watcher is not None:
            result = self._text_watcher.poll()
            if result is not None:
                self.dsx.apply_text(*result)
            return
        if self._text_searching or now - self._text_search_at < 10.0:
            return
        self._text_search_at = now
        self._text_searching = True

        def search():
            try:
                exe = find_process_exe(prof.exe)
                found = find_mod_text_file(exe.parent / prof.mod_text_search) if exe else None
                if found is not None and self.profile is prof:
                    self.mod_text_file = found
                    self._text_watcher = TextFileWatcher(found)
                    self.note("Using trigger mod file %s" % found)
            finally:
                self._text_searching = False

        threading.Thread(target=search, name="mod-file-search", daemon=True).start()

    # -------------------------------------------------------------- status
    def status(self) -> dict:
        c = self.controller
        return {
            "connected": c.connected,
            "transport": "Bluetooth" if c.transport == BLUETOOTH else ("USB" if c.transport == USB else "-"),
            "battery": c.input.battery if c.connected else None,
            "charging": c.input.charging,
            "haptics": self.output.description if self.output else (self.output_note or "Not active"),
            "haptics_note": self.output_note,
            "profile": self.profile.name,
            "game": self.detected_game,
            "integration": self.integration.name if self.integration else None,
            "integration_status": self.integration.status() if self.integration else "",
            "audio": ("Listening to %s" % self.capture.device_name) if self.capture.running
                     else (self.capture.error or "Off"),
            "xbox": "Virtual Xbox controller active" if self.xbox.running else (self.xbox.error or "Off"),
            "mods": (("Receiving from a game mod" if self.dsx.receiving else "Listening on port %d" % self.dsx.port)
                     if self.dsx.running else (self.dsx.error or "Off")),
            "haptic_peak": self.mixer.peak,
            "reports_per_second": c.reports_per_second,
        }
