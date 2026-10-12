"""Shared plumbing for game integrations."""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..haptics import synth
from ..triggers import TriggerEffect

log = logging.getLogger(__name__)


@dataclass
class Outputs:
    """What an integration wants this frame. ``None`` = no opinion (profile decides)."""

    left_trigger: TriggerEffect | None = None
    right_trigger: TriggerEffect | None = None
    lightbar: tuple[int, int, int] | None = None
    player_leds: int | None = None
    mute_audio: bool = False  # pause sound-based haptics (e.g. during cutscenes)


class Integration:
    id = "base"
    name = "Base"
    games = ""  # human readable list of supported games
    setup: list[str] = []

    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = dict(settings or {})
        self.last_data = 0.0
        self.packets = 0

    @property
    def receiving(self) -> bool:
        return time.monotonic() - self.last_data < 1.0

    def start(self, ctx) -> None:
        pass

    def stop(self, ctx) -> None:
        ctx.mixer.silence(self.id + ".")

    def tick(self, ctx, dt: float) -> Outputs:
        return Outputs()

    def status(self) -> str:
        if self.receiving:
            return "Receiving telemetry (%d packets)" % self.packets
        return "Waiting for game data..."

    def setting(self, key: str, default: float) -> float:
        return float(self.settings.get(key, default))


class UdpListener:
    """Receives telemetry datagrams on a background thread and keeps the latest one."""

    def __init__(self, port: int, on_packet):
        self.port = port
        self.on_packet = on_packet
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.error: str | None = None

    def start(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.5)
        except OSError as exc:
            self.error = "Could not listen on UDP port %d: %s" % (self.port, exc)
            log.warning(self.error)
            return
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._run, name="udp-%d" % self.port, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.5)
        if self._sock:
            self._sock.close()
            self._sock = None

    def _run(self) -> None:
        while self._running:
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.on_packet(data)
            except Exception:
                log.exception("telemetry parse failed")


# ------------------------------------------------------------------ racing

WHEELS = ("FL", "FR", "RL", "RR")


@dataclass
class CarTelemetry:
    """Game-independent car state. Wheel lists are ordered FL, FR, RL, RR."""

    active: bool = False
    rpm: float = 0.0
    max_rpm: float = 8000.0
    idle_rpm: float = 900.0
    speed: float = 0.0  # m/s
    throttle: float = 0.0  # 0-1
    brake: float = 0.0  # 0-1
    gear: int = 0
    # Grip usage per wheel: |value| >= 1 means the tyre is past the limit.
    # Positive = wheelspin, negative = lock-up (when the game tells us the sign).
    slip: list[float] = field(default_factory=lambda: [0.0] * 4)
    rumble_strip: list[float] = field(default_factory=lambda: [0.0] * 4)  # 0-1 kerb intensity
    surface: list[float] = field(default_factory=lambda: [0.0] * 4)  # 0-1 roughness (gravel/grass)
    puddle: list[float] = field(default_factory=lambda: [0.0] * 4)
    suspension: list[float] | None = None  # 0-1 normalised travel
    abs_active: bool | None = None
    tc_active: bool | None = None
    rev_lights: float | None = None  # 0-1 if the game provides shift lights
    collision: float = 0.0  # one-shot impact strength 0-1
    events: list[str] = field(default_factory=list)  # one-shot events e.g. "lights_out"


def _side_pan(i: int) -> float:
    return -1.0 if i % 2 == 0 else 1.0


class RacingFeel:
    """Turns ``CarTelemetry`` into haptics, trigger effects and lights.

    Tunable keys in ``settings`` (all 0-1 unless noted):
    engine, road, kerbs, impacts, slip_haptics, triggers (bool), lightbar (bool),
    brake_stiffness, slip_threshold (default 1.0).
    """

    def __init__(self, prefix: str, settings: dict[str, Any]):
        self.prefix = prefix
        self.settings = settings
        self._gear = None
        self._susp_prev: list[float] | None = None
        self._flash = 0.0
        self._was_active = False

    def _s(self, key: str, default: float) -> float:
        return float(self.settings.get(key, default))

    def _chan(self, ctx, name, factory=synth.ContinuousTone):
        return ctx.mixer.channel(self.prefix + name, factory)

    def update(self, ctx, t: CarTelemetry, dt: float) -> Outputs:
        out = Outputs()
        if not t.active:
            if self._was_active:
                ctx.mixer.silence(self.prefix)
                self._was_active = False
            out.left_trigger = out.right_trigger = TriggerEffect()
            return out
        self._was_active = True
        mixer = ctx.mixer
        span = max(t.max_rpm - t.idle_rpm, 1.0)
        rpm_frac = min(max((t.rpm - t.idle_rpm) / span, 0.0), 1.0)
        speed_kmh = t.speed * 3.6
        slip_thr = self._s("slip_threshold", 1.0)

        # Engine: low rumble that rises in pitch with RPM.
        engine = self._chan(ctx, "engine")
        engine.harmonics = 0.6
        engine.set(freq=22 + 120 * rpm_frac, amp=self._s("engine", 0.35) * (0.15 + 0.35 * t.throttle + 0.15 * rpm_frac))

        # Road texture grows with speed; rough surfaces add more.
        road = self._chan(ctx, "road", synth.ContinuousNoise)
        rough = max(t.surface) if t.surface else 0.0
        puddle = max(t.puddle) if t.puddle else 0.0
        road.set(amp=self._s("road", 0.5) * (min(speed_kmh / 250, 0.25) + rough * 0.6 + puddle * 0.3),
                 cutoff=40 + min(speed_kmh, 300) * 0.5 + rough * 80)

        # Kerbs / rumble strips: rhythmic per-side buzz, rate tied to speed.
        kerb_rate = min(max(speed_kmh / 3.0, 12), 70)
        for side, idx in (("l", (0, 2)), ("r", (1, 3))):
            kerb = self._chan(ctx, "kerb_" + side)
            kerb.harmonics = 1.0
            level = max(t.rumble_strip[i] for i in idx)
            kerb.set(freq=kerb_rate, amp=self._s("kerbs", 0.8) * min(level, 1.0),
                     pan=-1 if side == "l" else 1)

        # Wheelspin / lock-up: a high buzz you feel before you see the slide.
        over = max(max(abs(s) for s in t.slip) - slip_thr * 0.8, 0.0)
        slipch = self._chan(ctx, "slip")
        slipch.set(freq=140 + 60 * min(over, 1.0), amp=self._s("slip_haptics", 0.5) * min(over * 1.5, 1.0))

        # Suspension hits.
        if t.suspension is not None:
            if self._susp_prev is not None and dt > 0:
                for i in range(4):
                    jolt = abs(t.suspension[i] - self._susp_prev[i])
                    if jolt > 0.18:
                        mixer.play(synth.thump(min(jolt * 2, 1.0) * self._s("impacts", 0.8), _side_pan(i)))
            self._susp_prev = list(t.suspension)

        if t.collision > 0:
            mixer.play(synth.impact(min(t.collision, 1.0) * self._s("impacts", 0.8) + 0.2, duration=0.35))

        if self._gear is not None and t.gear != self._gear:
            mixer.play(synth.Tone(90, 0.9 * self._s("impacts", 0.8), 0.06, attack=0.001, release=0.04))
        self._gear = t.gear

        for ev in t.events:
            if ev == "lights_out":
                mixer.play(synth.buzz(0.8, 0.3, 180))

        # Adaptive triggers.
        if self.settings.get("triggers", True):
            out.left_trigger = self._brake_trigger(t, slip_thr, speed_kmh)
            out.right_trigger = self._throttle_trigger(t, slip_thr, rpm_frac)

        # Lightbar as shift light, player LEDs as a rev bar.
        if self.settings.get("lightbar", True):
            shift = t.rev_lights if t.rev_lights is not None else rpm_frac
            out.lightbar, out.player_leds = self._shift_lights(shift, dt)
        return out

    def _brake_trigger(self, t: CarTelemetry, thr: float, speed_kmh: float) -> TriggerEffect:
        front = [t.slip[0], t.slip[1]]
        locking = t.brake > 0.1 and speed_kmh > 8 and min(front) < -thr * 0.9
        if t.abs_active or locking:
            return TriggerEffect("vibration", {"position": 1, "amplitude": 8, "frequency": 22})
        stiff = self._s("brake_stiffness", 0.7)
        end_strength = max(1, round(3 + 5 * stiff))
        return TriggerEffect("slope", {"start": 1, "end": 7, "start_strength": 1, "end_strength": end_strength})

    def _throttle_trigger(self, t: CarTelemetry, thr: float, rpm_frac: float) -> TriggerEffect:
        rear = max(t.slip[2], t.slip[3])
        if t.tc_active or rear > thr:
            amount = min(max(rear - thr, 0.0) + (0.5 if t.tc_active else 0.0), 1.0)
            freq = 5 * round((30 + 40 * amount) / 5)
            return TriggerEffect("vibration", {"position": 2, "amplitude": round(4 + 4 * amount), "frequency": freq})
        if rpm_frac > 0.985 and t.throttle > 0.5:  # bouncing off the limiter
            return TriggerEffect("vibration", {"position": 3, "amplitude": 4, "frequency": 80})
        return TriggerEffect("feedback", {"position": 2, "strength": 2})

    def _shift_lights(self, shift: float, dt: float) -> tuple[tuple[int, int, int], int]:
        leds = 0
        for i in range(5):
            if shift > 0.35 + i * 0.13:
                leds |= 1 << i
        if shift >= 0.97:
            self._flash = (self._flash + dt * 8) % 2
            on = self._flash < 1
            return ((255, 255, 255) if on else (255, 0, 0)), (0x1F if on else 0)
        if shift < 0.5:
            return (0, 200, 40), leds
        if shift < 0.8:
            k = (shift - 0.5) / 0.3
            return (int(255 * k), int(200 * (1 - k * 0.3)), 0), leds
        k = (shift - 0.8) / 0.17
        return (255, int(140 * (1 - k)), 0), leds
