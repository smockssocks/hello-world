"""Game audio capture (WASAPI loopback) and audio-to-haptics conversion.

This is what makes haptics work in *any* game: we listen to what the PC is
playing, keep the low frequencies (explosions, engines, footsteps, gunfire) and
feed them to the actuators, left channel to left actuator, right to right.
"""

from __future__ import annotations

import logging
import threading
from collections import deque

import numpy as np

from .synth import MovingAverage, Source

log = logging.getLogger(__name__)


class AudioCapture:
    """Captures whatever the default Windows playback device is playing."""

    MAX_BACKLOG_S = 0.08
    TARGET_BACKLOG_S = 0.025

    def __init__(self):
        self._pa = None
        self._stream = None
        self._fifo: deque[np.ndarray] = deque()
        self._fifo_frames = 0
        self._lock = threading.Lock()
        self.rate = 48000
        self.device_name = ""
        self.running = False
        self.error: str | None = None
        # Live features for reactive effects.
        self.bass = 0.0  # smoothed low-frequency energy 0..1-ish
        self.loudness = 0.0
        self.transient = 0.0  # >0 on sudden hits, decays quickly
        self._bass_slow = 0.0
        self._bass_filter: MovingAverage | None = None
        self.listeners: list = []  # callables(features) called from the audio thread

    def start(self) -> bool:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.error = "pyaudiowpatch is not installed (Windows only)"
            return False
        try:
            self._pa = pyaudio.PyAudio()
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if any(k in speakers["name"].lower() for k in ("wireless controller", "dualsense")):
                self.error = ("Windows is using the controller as its default speaker. Switch the default "
                              "playback device back to your headphones/speakers.")
                self._close()
                return False
            if not speakers.get("isLoopbackDevice"):
                for lb in self._pa.get_loopback_device_info_generator():
                    if speakers["name"] in lb["name"]:
                        speakers = lb
                        break
                else:
                    self.error = "No loopback device found for '%s'" % speakers["name"]
                    self._close()
                    return False
            self.device_name = speakers["name"]
            self.rate = int(speakers["defaultSampleRate"])
            channels = int(speakers["maxInputChannels"])
            self._bass_filter = MovingAverage(self.rate / 300)

            def callback(data, frame_count, _time, _status):
                frames = np.frombuffer(data, dtype=np.float32).reshape(frame_count, channels)
                self._push(_to_stereo(frames))
                return None, pyaudio.paContinue

            self._stream = self._pa.open(format=pyaudio.paFloat32, channels=channels, rate=self.rate,
                                         input=True, input_device_index=speakers["index"],
                                         frames_per_buffer=512, stream_callback=callback)
            self._stream.start_stream()
        except Exception as exc:
            self.error = "Audio capture failed: %s" % exc
            self._close()
            return False
        self.running = True
        log.info("Capturing game audio from '%s'", self.device_name)
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
        self._close()

    def _close(self) -> None:
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    # ------------------------------------------------------------- buffering
    def _push(self, stereo: np.ndarray) -> None:
        self._analyse(stereo)
        with self._lock:
            self._fifo.append(stereo)
            self._fifo_frames += len(stereo)
            max_frames = int(self.MAX_BACKLOG_S * self.rate)
            if self._fifo_frames > max_frames:
                # Consumer is behind (or not running): drop old audio to keep latency low.
                target = int(self.TARGET_BACKLOG_S * self.rate)
                while self._fifo and self._fifo_frames - len(self._fifo[0]) >= target:
                    self._fifo_frames -= len(self._fifo.popleft())

    def pull(self, frames: int) -> np.ndarray:
        """Take up to ``frames`` stereo frames; pads with zeros on underflow."""
        out = np.zeros((frames, 2), dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < frames and self._fifo:
                chunk = self._fifo[0]
                take = min(len(chunk), frames - filled)
                out[filled:filled + take] = chunk[:take]
                filled += take
                if take == len(chunk):
                    self._fifo.popleft()
                else:
                    self._fifo[0] = chunk[take:]
                self._fifo_frames -= take
        return out

    def _analyse(self, stereo: np.ndarray) -> None:
        mono = stereo.mean(axis=1).astype(np.float64)
        low = self._bass_filter.process(mono) if self._bass_filter else mono
        bass = float(np.sqrt(np.mean(low ** 2))) * 4 if len(low) else 0.0
        self.loudness = 0.8 * self.loudness + 0.2 * float(np.sqrt(np.mean(mono ** 2))) * 3
        self.bass = 0.6 * self.bass + 0.4 * bass
        # Transient: fast bass envelope jumping well above the slow average.
        self._bass_slow = 0.97 * self._bass_slow + 0.03 * bass
        jump = bass - self._bass_slow * 1.8 - 0.02
        self.transient = max(self.transient * 0.7, jump if jump > 0 else 0.0)
        for fn in self.listeners:
            try:
                fn(self)
            except Exception:
                log.exception("audio listener failed")


def _to_stereo(frames: np.ndarray) -> np.ndarray:
    ch = frames.shape[1]
    if ch == 1:
        return np.repeat(frames, 2, axis=1)
    stereo = frames[:, :2].copy()
    if ch >= 6:  # 5.1 / 7.1: fold centre and LFE (where explosions live) into both sides
        stereo += (frames[:, 2:3] * 0.7 + frames[:, 3:4])
    return stereo


class AudioPassthrough(Source):
    """Turns captured game audio into haptic samples at the output's sample rate."""

    def __init__(self, capture: AudioCapture, gain: float = 1.0, cutoff: float = 250.0, gate: float = 0.01):
        self.capture = capture
        self.gain = gain
        self.cutoff = cutoff
        self.gate = gate
        self._filters: tuple[MovingAverage, MovingAverage] | None = None
        self._filter_key = None
        self._frac = 0.0
        self._last = np.zeros((1, 2))
        self._gate_gain = 0.0

    def render(self, n: int, sr: int) -> np.ndarray:
        src_rate = self.capture.rate
        exact = n * src_rate / sr + self._frac
        need = int(exact)
        self._frac = exact - need
        raw = self.capture.pull(need).astype(np.float64)
        key = (src_rate, self.cutoff)
        if self._filter_key != key:
            width = src_rate / self.cutoff / 2
            self._filters = (MovingAverage(width, 2), MovingAverage(width, 2))
            self._filter_key = key
        f1, f2 = self._filters
        low = f2.process(f1.process(raw)) * self.gain * 2.5
        rms = float(np.sqrt(np.mean(low ** 2))) if len(low) else 0.0
        # Noise gate: quiet background music/ambience shouldn't buzz constantly.
        target = 1.0 if rms > self.gate else 0.0
        self._gate_gain += (target - self._gate_gain) * (0.5 if target else 0.15)
        low *= self._gate_gain
        self.level = min(rms * 3 * self._gate_gain, 1.0)
        if n == 0:
            return np.zeros((0, 2))
        if len(low) < 2:
            return np.repeat(self._last, n, axis=0)
        x_src = np.linspace(0, 1, len(low))
        x_dst = np.linspace(0, 1, n)
        out = np.stack([np.interp(x_dst, x_src, low[:, 0]), np.interp(x_dst, x_src, low[:, 1])], axis=1)
        self._last = out[-1:]
        return out

    def rumble(self):
        lv = self.level
        return lv, lv, lv * 0.4, lv * 0.4
