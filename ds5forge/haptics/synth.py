"""Haptic waveform synthesis.

All effects render at whatever sample rate the active output needs
(48 kHz for USB audio, 3 kHz for Bluetooth), so the same effect feels the
same on both transports. Output is stereo float in [-1, 1]:
column 0 drives the left actuator, column 1 the right one.
"""

from __future__ import annotations

import math
import threading
import time

import numpy as np

# Frequencies below this mostly map to the strong (left) rumble motor when we
# have to fall back to classic rumble.
LOW_BAND_HZ = 110.0


def pan_gains(pan: float) -> tuple[float, float]:
    """pan -1 = left only, 0 = both, 1 = right only."""
    pan = min(max(pan, -1.0), 1.0)
    return min(1.0, 1.0 - pan), min(1.0, 1.0 + pan)


class MovingAverage:
    """Streaming boxcar low-pass filter, vectorised, keeps state between blocks."""

    def __init__(self, width: int, channels: int = 1):
        self.width = max(1, int(width))
        self._tail = np.zeros((self.width - 1, channels), dtype=np.float64)

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.width == 1:
            return x
        x2 = x.reshape(len(x), -1)
        buf = np.concatenate([self._tail, x2])
        c = np.cumsum(buf, axis=0)
        c = np.concatenate([np.zeros((1, c.shape[1])), c])
        out = (c[self.width:] - c[:-self.width]) / self.width
        self._tail = buf[len(buf) - (self.width - 1):] if self.width > 1 else self._tail
        return out.reshape(x.shape)


class Source:
    """Base class for anything that produces haptic samples."""

    done = False
    level = 0.0  # last rendered intensity 0-1, used for rumble fallback
    band_low = True  # whether that intensity belongs to the low/strong motor

    def render(self, n: int, sr: int) -> np.ndarray:
        raise NotImplementedError

    def rumble(self) -> tuple[float, float, float, float]:
        """(low_left, low_right, high_left, high_right) for rumble fallback."""
        return 0.0, 0.0, 0.0, 0.0


class Tone(Source):
    """One-shot sine with attack/release envelope and optional pitch sweep."""

    def __init__(self, freq: float, amp: float, duration: float, pan: float = 0.0,
                 attack: float = 0.004, release: float = 0.04, freq_end: float | None = None):
        self.freq, self.freq_end = freq, freq if freq_end is None else freq_end
        self.amp, self.duration = amp, max(duration, 0.001)
        self.attack, self.release = attack, release
        self.gl, self.gr = pan_gains(pan)
        self._t = 0.0
        self._phase = 0.0
        self.band_low = (self.freq + self.freq_end) / 2 < LOW_BAND_HZ

    def _envelope(self, t: np.ndarray) -> np.ndarray:
        env = np.ones_like(t)
        if self.attack > 0:
            env = np.minimum(env, t / self.attack)
        if self.release > 0:
            env = np.minimum(env, (self.duration - t) / self.release)
        return np.clip(env, 0.0, 1.0)

    def render(self, n: int, sr: int) -> np.ndarray:
        t = self._t + np.arange(n) / sr
        frac = np.clip(t / self.duration, 0, 1)
        freq = self.freq + (self.freq_end - self.freq) * frac
        phase = self._phase + 2 * np.pi * np.cumsum(freq) / sr
        self._phase = float(phase[-1]) % (2 * np.pi) if n else self._phase
        env = self._envelope(t)
        wave = np.sin(phase) * env * self.amp
        self._t = float(t[-1] + 1 / sr) if n else self._t
        if self._t >= self.duration:
            self.done = True
        self.level = float(env.max()) * self.amp if n else 0.0
        return np.stack([wave * self.gl, wave * self.gr], axis=1)

    def rumble(self):
        lv = self.level
        if self.band_low:
            return lv * self.gl, lv * self.gr, 0.0, 0.0
        return 0.0, 0.0, lv * self.gl, lv * self.gr


class NoiseBurst(Source):
    """One-shot low-passed noise - impacts, crashes, thuds."""

    def __init__(self, amp: float, duration: float, cutoff: float = 150.0, pan: float = 0.0,
                 attack: float = 0.002, release: float | None = None):
        self.amp, self.duration, self.cutoff = amp, max(duration, 0.001), cutoff
        self.attack = attack
        self.release = duration * 0.8 if release is None else release
        self.gl, self.gr = pan_gains(pan)
        self._t = 0.0
        self._filters: dict[int, MovingAverage] = {}
        self._rng = np.random.default_rng()
        self.band_low = cutoff < LOW_BAND_HZ * 2

    def render(self, n: int, sr: int) -> np.ndarray:
        filt = self._filters.get(sr)
        if filt is None:
            filt = self._filters[sr] = MovingAverage(sr / max(self.cutoff, 1.0) / 2)
        noise = filt.process(self._rng.uniform(-1, 1, n))
        # Boxcar filtering reduces amplitude roughly with sqrt(width); compensate.
        noise *= math.sqrt(filt.width) * 0.9
        t = self._t + np.arange(n) / sr
        env = np.clip(np.minimum(t / max(self.attack, 1e-4), (self.duration - t) / max(self.release, 1e-4)), 0, 1)
        wave = np.clip(noise, -1, 1) * env * self.amp
        self._t = float(t[-1] + 1 / sr) if n else self._t
        if self._t >= self.duration:
            self.done = True
        self.level = float(env.max()) * self.amp if n else 0.0
        return np.stack([wave * self.gl, wave * self.gr], axis=1)

    def rumble(self):
        lv = self.level
        return lv * self.gl, lv * self.gr, lv * 0.5 * self.gl, lv * 0.5 * self.gr


class ContinuousTone(Source):
    """A persistent tone whose frequency/amplitude/pan can be changed live (engines, motors)."""

    def __init__(self, freq: float = 60.0, amp: float = 0.0, pan: float = 0.0, harmonics: float = 0.0):
        self.freq, self.target_amp, self.pan = freq, amp, pan
        self.harmonics = harmonics  # 0 = pure sine, 1 = rough square-ish
        self._amp = 0.0
        self._phase = 0.0

    def set(self, freq: float | None = None, amp: float | None = None, pan: float | None = None) -> None:
        if freq is not None:
            self.freq = max(1.0, freq)
        if amp is not None:
            self.target_amp = min(max(amp, 0.0), 1.0)
        if pan is not None:
            self.pan = pan

    def render(self, n: int, sr: int) -> np.ndarray:
        amps = np.linspace(self._amp, self.target_amp, n, endpoint=False) if n else np.zeros(0)
        self._amp = self.target_amp
        phase = self._phase + 2 * np.pi * self.freq * np.arange(1, n + 1) / sr
        self._phase = float(phase[-1]) % (2 * np.pi) if n else self._phase
        wave = np.sin(phase)
        if self.harmonics > 0:
            wave = wave + self.harmonics * 0.4 * np.sin(2 * phase) + self.harmonics * 0.25 * np.sign(wave)
            wave /= 1 + self.harmonics * 0.65
        wave *= amps
        gl, gr = pan_gains(self.pan)
        self.level = self.target_amp
        self.band_low = self.freq < LOW_BAND_HZ
        return np.stack([wave * gl, wave * gr], axis=1)

    def rumble(self):
        gl, gr = pan_gains(self.pan)
        lv = self.level
        if self.freq < LOW_BAND_HZ:
            return lv * gl, lv * gr, 0.0, 0.0
        return 0.0, 0.0, lv * gl, lv * gr


class ContinuousNoise(Source):
    """Persistent textured noise (road surface, gravel, rain) with live parameters."""

    def __init__(self, amp: float = 0.0, cutoff: float = 120.0, pan: float = 0.0):
        self.target_amp, self.cutoff, self.pan = amp, cutoff, pan
        self._amp = 0.0
        self._filters: dict[tuple[int, int], MovingAverage] = {}
        self._rng = np.random.default_rng()

    def set(self, amp: float | None = None, cutoff: float | None = None, pan: float | None = None) -> None:
        if amp is not None:
            self.target_amp = min(max(amp, 0.0), 1.0)
        if cutoff is not None:
            self.cutoff = max(5.0, cutoff)
        if pan is not None:
            self.pan = pan

    def render(self, n: int, sr: int) -> np.ndarray:
        width = max(1, int(sr / self.cutoff / 2))
        key = (sr, width)
        filt = self._filters.get(key)
        if filt is None:
            self._filters = {key: MovingAverage(width, channels=2)}
            filt = self._filters[key]
        noise = filt.process(self._rng.uniform(-1, 1, (n, 2))) * math.sqrt(width) * 0.9
        amps = np.linspace(self._amp, self.target_amp, n, endpoint=False)[:, None] if n else np.zeros((0, 1))
        self._amp = self.target_amp
        gl, gr = pan_gains(self.pan)
        self.level = self.target_amp
        return np.clip(noise, -1, 1) * amps * np.array([gl, gr])

    def rumble(self):
        gl, gr = pan_gains(self.pan)
        lv = self.level
        return lv * gl * 0.7, lv * gr * 0.7, lv * gl * 0.5, lv * gr * 0.5


class HapticMixer:
    """Mixes one-shot and persistent sources into a stereo haptic stream."""

    def __init__(self):
        self._lock = threading.Lock()
        self._oneshots: list[Source] = []
        self._scheduled: list[tuple[float, Source]] = []
        self._channels: dict[str, Source] = {}
        self.gain = 1.0
        self.enabled = True
        self.peak = 0.0  # last block peak, for the UI meter

    # ------------------------------------------------------------------ control
    def play(self, source: Source, delay: float = 0.0) -> None:
        with self._lock:
            if delay > 0:
                self._scheduled.append((time.monotonic() + delay, source))
            else:
                self._oneshots.append(source)

    def channel(self, name: str, factory=ContinuousTone) -> Source:
        """Get (or create) a named persistent source, e.g. ``mixer.channel("engine")``."""
        with self._lock:
            src = self._channels.get(name)
            if src is None:
                src = self._channels[name] = factory()
            return src

    def set_channel(self, name: str, source: Source | None) -> None:
        with self._lock:
            if source is None:
                self._channels.pop(name, None)
            else:
                self._channels[name] = source

    def silence(self, prefix: str = "") -> None:
        """Drop one-shots and remove persistent channels whose name starts with ``prefix``."""
        with self._lock:
            self._oneshots.clear()
            self._scheduled.clear()
            for name in [n for n in self._channels if n.startswith(prefix)]:
                del self._channels[name]

    # --------------------------------------------------------------- rendering
    def _active_sources(self) -> list[Source]:
        now = time.monotonic()
        with self._lock:
            if self._scheduled:
                due = [s for t, s in self._scheduled if t <= now]
                self._scheduled = [(t, s) for t, s in self._scheduled if t > now]
                self._oneshots.extend(due)
            self._oneshots = [s for s in self._oneshots if not s.done]
            return list(self._channels.values()) + list(self._oneshots)

    def render(self, n: int, sr: int) -> np.ndarray:
        out = np.zeros((n, 2), dtype=np.float64)
        if not self.enabled:
            self.peak = 0.0
            return out.astype(np.float32)
        for src in self._active_sources():
            try:
                out += src.render(n, sr)
            except Exception:  # a broken effect should never stop the stream
                src.done = True
        out *= self.gain
        # Soft clip keeps loud mixes punchy without harsh square-wave clipping.
        out = np.tanh(out)
        self.peak = float(np.abs(out).max()) if n else 0.0
        return out.astype(np.float32)

    def rumble_levels(self) -> tuple[float, float]:
        """Approximate (strong, weak) motor levels for the classic-rumble fallback."""
        low = high = 0.0
        for src in self._active_sources():
            ll, lr, hl, hr = src.rumble()
            low += max(ll, lr)
            high += max(hl, hr)
        g = self.gain if self.enabled else 0.0
        return min(low * g, 1.0), min(high * g, 1.0)


# ------------------------------------------------------------- effect library

def click(amp: float = 0.8, pan: float = 0.0) -> Source:
    return Tone(230, amp, 0.025, pan=pan, attack=0.001, release=0.015)


def tick(amp: float = 0.5, pan: float = 0.0) -> Source:
    return Tone(320, amp, 0.012, pan=pan, attack=0.0005, release=0.008)


def thump(amp: float = 1.0, pan: float = 0.0) -> Source:
    return Tone(70, amp, 0.12, pan=pan, attack=0.002, release=0.09, freq_end=45)


def impact(amp: float = 1.0, pan: float = 0.0, duration: float = 0.25) -> Source:
    return NoiseBurst(amp, duration, cutoff=90, pan=pan)


def buzz(amp: float = 0.6, duration: float = 0.2, freq: float = 160, pan: float = 0.0) -> Source:
    return Tone(freq, amp, duration, pan=pan, attack=0.005, release=0.05)


def play_heartbeat(mixer: HapticMixer, amp: float = 0.9) -> None:
    mixer.play(Tone(55, amp, 0.09, release=0.07))
    mixer.play(Tone(50, amp * 0.7, 0.11, release=0.09), delay=0.18)


def play_rain(mixer: HapticMixer, drops: int = 25, seconds: float = 2.0) -> None:
    rng = np.random.default_rng()
    for _ in range(drops):
        mixer.play(Tone(rng.uniform(250, 400), rng.uniform(0.2, 0.6), 0.01, pan=rng.uniform(-1, 1),
                        attack=0.0005, release=0.006), delay=float(rng.uniform(0, seconds)))


def play_sweep(mixer: HapticMixer) -> None:
    mixer.play(Tone(40, 0.8, 1.0, pan=-1, freq_end=300))
    mixer.play(Tone(300, 0.8, 1.0, pan=1, freq_end=40), delay=1.1)


def play_ping_pong(mixer: HapticMixer, beats: int = 6) -> None:
    for i in range(beats):
        mixer.play(click(0.9, -1 if i % 2 == 0 else 1), delay=i * 0.18)


TEST_EFFECTS = {
    "Click": lambda m: m.play(click()),
    "Thump": lambda m: m.play(thump()),
    "Impact": lambda m: m.play(impact()),
    "Heartbeat": play_heartbeat,
    "Rain": play_rain,
    "Left / right": play_ping_pong,
    "Frequency sweep": play_sweep,
    "Engine rev": lambda m: m.play(Tone(30, 0.7, 1.5, freq_end=180, release=0.3)),
}
