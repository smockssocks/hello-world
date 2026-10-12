"""Haptic output backends.

* ``UsbAudioOutput``  - USB: the DualSense is a 4-channel sound card; channels 3+4
  drive the left/right voice-coil actuators. Full fidelity.
* ``BluetoothHapticsOutput`` - Bluetooth: streams 3 kHz 8-bit audio inside HID
  report 0x32. Experimental but gives real haptics wirelessly.
* ``RumbleOutput`` - fallback for either transport: classic two-motor rumble
  emulation derived from the same effects.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

import numpy as np

from .. import protocol as p
from .synth import HapticMixer

log = logging.getLogger(__name__)

CONTROLLER_AUDIO_NAMES = ("wireless controller", "dualsense")


class HapticOutput:
    name = "none"
    description = ""

    def __init__(self, mixer: HapticMixer):
        self.mixer = mixer
        self.running = False
        self.error: str | None = None

    def start(self) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        self.running = False


class _HighResTimer:
    """Ask Windows for 1 ms timer resolution while streaming."""

    def __enter__(self):
        self._ok = False
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
                self._ok = True
            except Exception:
                pass
        return self

    def __exit__(self, *exc):
        if self._ok:
            import ctypes
            ctypes.windll.winmm.timeEndPeriod(1)


def _sleep_until(deadline: float) -> None:
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > 0.002:
            time.sleep(remaining - 0.0015)
        else:
            time.sleep(0)


class RumbleOutput(HapticOutput):
    name = "rumble"
    description = "Classic rumble (works everywhere, less detailed)"

    def __init__(self, mixer: HapticMixer, controller):
        super().__init__(mixer)
        self.controller = controller
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        self.running = True
        self._thread = threading.Thread(target=self._run, name="rumble-out", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=1)
        self.controller.set_rumble(0, 0)

    def _run(self) -> None:
        while self.running:
            # Rendering advances one-shot effects so their envelopes play out.
            self.mixer.render(10, 1000)
            low, high = self.mixer.rumble_levels()
            self.controller.set_rumble(low, high)
            time.sleep(0.01)


class BluetoothHapticsOutput(HapticOutput):
    name = "bt-haptics"
    description = "Bluetooth HD haptics (experimental)"

    def __init__(self, mixer: HapticMixer, controller):
        super().__init__(mixer)
        self.controller = controller
        self._thread: threading.Thread | None = None
        self.failures = 0

    def start(self) -> bool:
        self.running = True
        self._thread = threading.Thread(target=self._run, name="bt-haptics", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=1)

    @staticmethod
    def encode(block: np.ndarray) -> bytes:
        """(32, 2) float block -> 64 bytes of interleaved signed 8-bit PCM."""
        pcm = np.clip(np.round(block * 127), -127, 127).astype(np.int8)
        return pcm.reshape(-1).tobytes()

    def _run(self) -> None:
        counter = 0
        consecutive_failures = 0
        with _HighResTimer():
            deadline = time.perf_counter()
            while self.running:
                block = self.mixer.render(p.BT_HAPTICS_FRAMES, p.BT_HAPTICS_SAMPLE_RATE)
                report = p.build_bt_haptics_report(self.encode(block), counter)
                counter = (counter + 1) & 0xFF
                if self.controller.write_raw(report):
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    self.failures += 1
                    if consecutive_failures > 200:
                        self.error = "Controller rejected Bluetooth haptics reports"
                        log.warning(self.error)
                        self.running = False
                        break
                deadline += p.BT_HAPTICS_PERIOD
                now = time.perf_counter()
                if now - deadline > 0.1:  # fell far behind (e.g. system stall): resync
                    deadline = now
                _sleep_until(deadline)


class UsbAudioOutput(HapticOutput):
    name = "usb-audio"
    description = "USB HD haptics (best quality)"
    SAMPLE_RATE = 48000

    def __init__(self, mixer: HapticMixer):
        super().__init__(mixer)
        self._pa = None
        self._stream = None
        self.channels = 0
        self.device_name = ""

    @staticmethod
    def find_device(pa) -> dict | None:
        """Find the controller's speaker endpoint on the WASAPI host API."""
        try:
            import pyaudiowpatch as pyaudio
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            host_index = wasapi["index"]
        except Exception:
            host_index = None
        best = None
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = str(info.get("name", "")).lower()
            if info.get("maxOutputChannels", 0) < 1 or info.get("isLoopbackDevice"):
                continue
            if not any(k in name for k in CONTROLLER_AUDIO_NAMES):
                continue
            if host_index is not None and info.get("hostApi") != host_index:
                continue
            if best is None or info["maxOutputChannels"] > best["maxOutputChannels"]:
                best = info
        return best

    def start(self) -> bool:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.error = "pyaudiowpatch is not installed"
            return False
        self._pa = pyaudio.PyAudio()
        info = self.find_device(self._pa)
        if info is None:
            self.error = "Controller audio device not found (is it plugged in by USB?)"
            self._close_pa()
            return False
        self.device_name = info["name"]
        self.channels = int(info["maxOutputChannels"])
        if self.channels < 4:
            self.error = ("The controller's sound device is set to %d channels. In Windows Sound settings, "
                          "configure 'Wireless Controller' speakers as Quadraphonic (4 channels)." % self.channels)
            self._close_pa()
            return False
        rate = int(info.get("defaultSampleRate") or self.SAMPLE_RATE)
        channels = self.channels

        def callback(_in, frame_count, _time, _status):
            block = self.mixer.render(frame_count, rate)
            out = np.zeros((frame_count, channels), dtype=np.float32)
            out[:, 2] = block[:, 0]
            out[:, 3] = block[:, 1]
            return out.tobytes(), pyaudio.paContinue

        try:
            self._stream = self._pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate,
                                         output=True, output_device_index=info["index"],
                                         frames_per_buffer=256, stream_callback=callback)
            self._stream.start_stream()
        except Exception as exc:
            self.error = "Could not open controller audio: %s" % exc
            self._close_pa()
            return False
        self.running = True
        log.info("USB haptics streaming to '%s' (%d ch @ %d Hz)", self.device_name, channels, rate)
        return True

    def stop(self) -> None:
        self.running = False
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._close_pa()

    def _close_pa(self) -> None:
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
