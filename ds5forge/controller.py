"""DualSense connection management over hidapi (USB and Bluetooth)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import protocol as p

log = logging.getLogger(__name__)

USB = "usb"
BLUETOOTH = "bt"


def _load_hid():
    try:
        import hid  # provided by the "hidapi" package
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("The 'hidapi' package is missing. Run: pip install hidapi") from exc
    return hid


def find_controllers(hid_module=None) -> list[dict]:
    hid_module = hid_module or _load_hid()
    found = []
    for pid in p.SUPPORTED_PIDS:
        for info in hid_module.enumerate(p.SONY_VID, pid):
            usage_page = info.get("usage_page", 1)
            usage = info.get("usage", 5)
            if usage_page not in (0, 1) or usage not in (0, 5):
                continue
            found.append(info)
    return found


def guess_transport(info: dict) -> str:
    bus = info.get("bus_type")
    if bus == 2:
        return BLUETOOTH
    if bus == 1:
        return USB
    # Older hidapi: USB exposes interface 3, Bluetooth reports -1.
    return BLUETOOTH if info.get("interface_number", -1) == -1 else USB


class Controller:
    """Owns the HID handle, an I/O thread, input state and the desired output state."""

    def __init__(self, hid_module=None):
        self._hid_module = hid_module
        self._dev = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = p.OutputState()
        self._pending = p.UpdateMask()
        self._bt_seq = 0
        self._last_flush = 0.0
        self.input = p.InputState()
        self.transport: str | None = None
        self.product = ""
        self.connected = False
        self.reports_per_second = 0.0
        self._input_listeners: list[Callable[[p.InputState], None]] = []
        self._connection_listeners: list[Callable[[bool], None]] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ds5-io", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._reset_and_close()

    def on_input(self, fn: Callable[[p.InputState], None]) -> None:
        self._input_listeners.append(fn)

    def on_connection(self, fn: Callable[[bool], None]) -> None:
        self._connection_listeners.append(fn)

    # --------------------------------------------------------------- output API
    def set_rumble(self, left: float, right: float) -> None:
        """Classic rumble emulation, values 0-1 (left = strong/low, right = weak/high)."""
        lv, rv = int(min(max(left, 0), 1) * 255), int(min(max(right, 0), 1) * 255)
        with self._state_lock:
            if (lv, rv) != (self._state.rumble_left, self._state.rumble_right):
                self._state.rumble_left, self._state.rumble_right = lv, rv
                self._pending.rumble = True

    def set_triggers(self, left: bytes | None = None, right: bytes | None = None) -> None:
        with self._state_lock:
            if left is not None and bytes(left) != self._state.left_trigger:
                self._state.left_trigger = bytes(left)
                self._pending.left_trigger = True
            if right is not None and bytes(right) != self._state.right_trigger:
                self._state.right_trigger = bytes(right)
                self._pending.right_trigger = True

    def set_lightbar(self, r: int, g: int, b: int) -> None:
        rgb = (int(r), int(g), int(b))
        with self._state_lock:
            if rgb != self._state.lightbar:
                self._state.lightbar = rgb
                self._pending.lightbar = True

    def set_player_leds(self, mask: int) -> None:
        with self._state_lock:
            if mask != self._state.player_leds:
                self._state.player_leds = mask
                self._pending.player_leds = True

    def set_mic_led(self, mode: int) -> None:
        with self._state_lock:
            if mode != self._state.mic_led:
                self._state.mic_led = mode
                self._pending.mic_led = True

    def set_led_brightness(self, level: int) -> None:
        with self._state_lock:
            if level != self._state.led_brightness:
                self._state.led_brightness = level
                self._pending.led_brightness = True

    def write_raw(self, data: bytes) -> bool:
        """Write a pre-built report (used by the Bluetooth haptics stream)."""
        dev = self._dev
        if dev is None:
            return False
        with self._io_lock:
            try:
                return dev.write(bytes(data)) > 0
            except (OSError, ValueError) as exc:
                log.debug("write failed: %s", exc)
                return False

    # ------------------------------------------------------------------ internal
    def _run(self) -> None:
        counter, window_start = 0, time.monotonic()
        while self._running:
            if self._dev is None:
                if not self._try_connect():
                    time.sleep(1.0)
                    continue
            try:
                data = self._dev.read(100, 4)
            except (OSError, ValueError) as exc:
                log.warning("Controller disconnected (%s)", exc)
                self._drop_connection()
                continue
            if data:
                state = p.parse_input(data)
                if state is not None:
                    if data[0] == p.BT_INPUT_REPORT_ID:
                        self.transport = BLUETOOTH
                    self.input = state
                    counter += 1
                    for fn in self._input_listeners:
                        try:
                            fn(state)
                        except Exception:  # listener bugs must not kill I/O
                            log.exception("input listener failed")
            now = time.monotonic()
            if now - window_start >= 1.0:
                self.reports_per_second = counter / (now - window_start)
                counter, window_start = 0, now
            self._flush(now)

    def _flush(self, now: float, force: bool = False) -> None:
        min_interval = 0.004 if self.transport == USB else 0.008
        if not force and now - self._last_flush < min_interval:
            return
        with self._state_lock:
            if not self._pending.any():
                return
            mask, self._pending = self._pending, p.UpdateMask()
            state = p.OutputState(**vars(self._state))
        if self.transport == BLUETOOTH:
            report = p.build_bt_report(state, mask, self._bt_seq)
            self._bt_seq = (self._bt_seq + 1) & 0x0F
        else:
            report = p.build_usb_report(state, mask)
        if not self.write_raw(report):
            with self._state_lock:  # retry next round
                for k, v in vars(mask).items():
                    if v:
                        setattr(self._pending, k, True)
        self._last_flush = now

    def _try_connect(self) -> bool:
        try:
            hid_module = self._hid_module or _load_hid()
            self._hid_module = hid_module
            infos = find_controllers(hid_module)
        except Exception as exc:
            log.debug("enumerate failed: %s", exc)
            return False
        for info in infos:
            try:
                dev = hid_module.device()
                dev.open_path(info["path"])
            except (OSError, IOError) as exc:
                log.debug("open failed: %s", exc)
                continue
            self._dev = dev
            self.transport = guess_transport(info)
            self.product = info.get("product_string") or "DualSense"
            if self.transport == BLUETOOTH:
                # Reading the calibration feature report switches the controller
                # from the reduced BT report (0x01) to the full one (0x31).
                try:
                    dev.get_feature_report(p.CALIBRATION_FEATURE_REPORT_ID, p.CALIBRATION_FEATURE_REPORT_SIZE)
                except (OSError, ValueError):
                    pass
            with self._state_lock:
                self._pending = p.UpdateMask.all()
                self._pending.release_lightbar = True
                # Rumble flags switch the actuators to rumble emulation, which can
                # mute audio-driven HD haptics, so only send them when actually rumbling.
                self._pending.rumble = bool(self._state.rumble_left or self._state.rumble_right)
            self.connected = True
            log.info("Connected to %s over %s", self.product, "Bluetooth" if self.transport == BLUETOOTH else "USB")
            self._notify_connection(True)
            return True
        return False

    def _drop_connection(self) -> None:
        dev, self._dev = self._dev, None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass
        if self.connected:
            self.connected = False
            self.input = p.InputState()
            self._notify_connection(False)

    def _notify_connection(self, connected: bool) -> None:
        for fn in self._connection_listeners:
            try:
                fn(connected)
            except Exception:
                log.exception("connection listener failed")

    def _reset_and_close(self) -> None:
        if self._dev is not None:
            self.set_rumble(0, 0)
            self.set_triggers(p.TRIGGER_OFF, p.TRIGGER_OFF)
            self.set_lightbar(0, 0, 64)
            self.set_player_leds(0)
            self._flush(time.monotonic(), force=True)
        self._drop_connection()
