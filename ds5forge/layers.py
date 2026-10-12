"""Game-agnostic layers that work in any game: audio haptics and the Xbox rumble bridge."""

from __future__ import annotations

import logging
import time

from .haptics import synth
from .haptics.audio import AudioCapture, AudioPassthrough
from .protocol import InputState
from .triggers import TriggerEffect

log = logging.getLogger(__name__)


class AudioLayer:
    """Feeds game audio to the actuators and (optionally) kicks the triggers on gunshots."""

    CHANNEL = "layer.audio"

    def __init__(self, capture: AudioCapture):
        self.capture = capture
        self.source: AudioPassthrough | None = None
        self._kick_until = 0.0
        self._kick_side = "right"
        self._last_hit = 0.0

    def configure(self, mixer, enabled: bool, gain: float, cutoff: float) -> None:
        if enabled and self.capture.running:
            if self.source is None:
                self.source = AudioPassthrough(self.capture)
                mixer.set_channel(self.CHANNEL, self.source)
            self.source.gain = gain
            self.source.cutoff = cutoff
        elif self.source is not None:
            mixer.set_channel(self.CHANNEL, None)
            self.source = None

    def hits(self, mixer, enabled: bool, strength: float, threshold: float = 0.12) -> None:
        """Adds a crisp tap on sharp sounds (sword hits, impacts) on top of the raw audio rumble."""
        if not enabled or not self.capture.running:
            return
        now = time.monotonic()
        tr = self.capture.transient
        if tr > threshold and now - self._last_hit > 0.09:
            self._last_hit = now
            amp = min(1.0, strength * (0.5 + tr * 2))
            mixer.play(synth.Tone(150, amp, 0.035, attack=0.001, release=0.025, freq_end=90))

    def trigger_kick(self, inp: InputState, enabled: bool, threshold: float) -> tuple[TriggerEffect | None, TriggerEffect | None]:
        """Shooter helper: when a trigger is held and the audio has a sharp hit
        (gunfire, explosion) the held trigger kicks back."""
        if not enabled or not self.capture.running:
            return None, None
        now = time.monotonic()
        if self.capture.transient > threshold:
            if inp.r2 > 60:
                self._kick_until, self._kick_side = now + 0.07, "right"
            elif inp.l2 > 60:
                self._kick_until, self._kick_side = now + 0.07, "left"
        if now < self._kick_until:
            kick = TriggerEffect("vibration", {"position": 1, "amplitude": 8, "frequency": 40})
            return (kick, None) if self._kick_side == "left" else (None, kick)
        return None, None


XBOX_BUTTONS = {
    "cross": "XUSB_GAMEPAD_A", "circle": "XUSB_GAMEPAD_B",
    "square": "XUSB_GAMEPAD_X", "triangle": "XUSB_GAMEPAD_Y",
    "l1": "XUSB_GAMEPAD_LEFT_SHOULDER", "r1": "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "create": "XUSB_GAMEPAD_BACK", "options": "XUSB_GAMEPAD_START",
    "l3": "XUSB_GAMEPAD_LEFT_THUMB", "r3": "XUSB_GAMEPAD_RIGHT_THUMB",
    "ps": "XUSB_GAMEPAD_GUIDE", "touchpad": "XUSB_GAMEPAD_BACK",
    "up": "XUSB_GAMEPAD_DPAD_UP", "down": "XUSB_GAMEPAD_DPAD_DOWN",
    "left": "XUSB_GAMEPAD_DPAD_LEFT", "right": "XUSB_GAMEPAD_DPAD_RIGHT",
}


def _axis(v: int, invert: bool = False) -> int:
    x = (v - 128) * 258
    x = max(-32768, min(32767, x))
    return -x - 1 if invert and x != 0 else x


class XboxBridge:
    """Presents the DualSense as a virtual Xbox 360 pad (via ViGEmBus).

    Games that only know Xbox controllers then work normally, and their rumble
    commands are turned into rich DualSense haptics instead of plain rumble.
    """

    def __init__(self, mixer):
        self.mixer = mixer
        self.pad = None
        self.vg = None
        self.error: str | None = None
        self.large = 0.0
        self.small = 0.0
        self.last_rumble = 0.0

    @property
    def running(self) -> bool:
        return self.pad is not None

    def start(self) -> bool:
        if self.pad is not None:
            return True
        try:
            import vgamepad as vg
        except Exception as exc:
            self.error = ("Xbox bridge needs the 'vgamepad' package and the ViGEmBus driver "
                          "(pip install vgamepad): %s" % exc)
            return False
        self._low = self.mixer.channel("layer.xbox.low", synth.ContinuousTone)
        self._high = self.mixer.channel("layer.xbox.high", synth.ContinuousTone)
        self._low.harmonics = 0.5
        try:
            self.pad = vg.VX360Gamepad()
            self.vg = vg
            self.pad.register_notification(callback_function=self._on_rumble)
        except Exception as exc:
            self.error = "Could not create virtual Xbox controller (is ViGEmBus installed?): %s" % exc
            self.pad = None
            self.mixer.silence("layer.xbox")
            return False
        self.error = None
        log.info("Virtual Xbox 360 controller created")
        return True

    def stop(self) -> None:
        if self.pad is not None:
            try:
                self.pad.unregister_notification()
            except Exception:
                pass
            self.pad = None
        self.mixer.silence("layer.xbox")

    def _on_rumble(self, client, target, large_motor, small_motor, led_number, user_data):
        self.large, self.small = large_motor / 255.0, small_motor / 255.0
        self.last_rumble = time.monotonic()
        # Strong motor -> deep thump; weak motor -> fine high buzz. Both actuators.
        self._low.set(freq=55, amp=self.large)
        self._high.set(freq=180, amp=self.small * 0.8)

    def feed(self, inp: InputState) -> None:
        """Forward one DualSense input report to the virtual pad (called from the I/O thread)."""
        pad, vg = self.pad, self.vg
        if pad is None:
            return
        try:
            pad.left_joystick(x_value=_axis(inp.lx), y_value=_axis(inp.ly, invert=True))
            pad.right_joystick(x_value=_axis(inp.rx), y_value=_axis(inp.ry, invert=True))
            pad.left_trigger(value=inp.l2)
            pad.right_trigger(value=inp.r2)
            held = {xb for name, xb in XBOX_BUTTONS.items() if inp.pressed(name)}
            for xb in set(XBOX_BUTTONS.values()):
                btn = getattr(vg.XUSB_BUTTON, xb)
                if xb in held:
                    pad.press_button(button=btn)
                else:
                    pad.release_button(button=btn)
            pad.update()
        except Exception as exc:
            log.debug("xbox feed failed: %s", exc)

    def trigger_rumble(self, inp: InputState) -> tuple[TriggerEffect | None, TriggerEffect | None]:
        """Optional: echo strong rumble into a held trigger (impulse-trigger style)."""
        if self.pad is None or time.monotonic() - self.last_rumble > 0.5:
            return None, None
        level = max(self.large, self.small)
        if level < 0.25:
            return None, None
        eff = TriggerEffect("vibration", {"position": 1, "amplitude": round(2 + 6 * level), "frequency": 35})
        return (eff if inp.l2 > 40 else None), (eff if inp.r2 > 40 else None)
