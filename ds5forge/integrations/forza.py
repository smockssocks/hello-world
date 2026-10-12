"""Forza Horizon 4/5 and Forza Motorsport 7 / 2023 via the in-game "Data Out" UDP feed."""

from __future__ import annotations

import math
import struct
import threading
import time

from .base import CarTelemetry, Integration, Outputs, RacingFeel, UdpListener

SLED_SIZE = 232
SLED = struct.Struct("<iI" + "f" * 27 + "i" * 4 + "f" * 4 * 5 + "i" * 5)
DASH = struct.Struct("<" + "f" * 6 + "f" * 4 + "f" * 7 + "HBBBBBBbbb")

# Offset of the "dash" block per packet size.
DASH_OFFSETS = {
    311: 232,  # Forza Motorsport 7
    331: 232,  # Forza Motorsport (2023)
    324: 244,  # Forza Horizon 4 / 5 (12 extra bytes after the sled block)
}


def parse(data: bytes) -> dict | None:
    if len(data) < SLED_SIZE:
        return None
    v = SLED.unpack_from(data, 0)
    d = {
        "is_race_on": v[0] != 0,
        "max_rpm": v[2], "idle_rpm": v[3], "rpm": v[4],
        "accel": v[5:8], "velocity": v[8:11],
        "susp_norm": list(v[17:21]),
        "slip_ratio": list(v[21:25]),
        "on_rumble": list(v[29:33]),
        "puddle": list(v[33:37]),
        "surface_rumble": list(v[37:41]),
        "slip_angle": list(v[41:45]),
        "combined_slip": list(v[45:49]),
    }
    offset = DASH_OFFSETS.get(len(data))
    if offset is not None and len(data) >= offset + DASH.size:
        w = DASH.unpack_from(data, offset)
        d.update(speed=w[3], throttle=w[19] / 255, brake=w[20] / 255, gear=w[23], has_dash=True)
    else:
        vx, vy, vz = d["velocity"]
        d.update(speed=math.sqrt(vx * vx + vy * vy + vz * vz), throttle=0.0, brake=0.0, gear=0, has_dash=False)
    return d


class ForzaIntegration(Integration):
    id = "forza"
    name = "Forza (Horizon 4/5, Motorsport)"
    games = "Forza Horizon 4, Forza Horizon 5, Forza Motorsport 7, Forza Motorsport (2023)"
    setup = [
        "In the game open Settings > HUD and Gameplay (Horizon) or Settings > Gameplay & HUD (Motorsport).",
        "Set 'Data Out' to ON, 'Data Out IP Address' to 127.0.0.1 and 'Data Out IP Port' to 5300.",
        "Motorsport only: set 'Data Out Packet Format' to 'Dash'.",
        "Microsoft Store / Game Pass version only: run 'allow-forza-localhost.bat' once as administrator "
        "(Windows blocks Store apps from sending to localhost).",
    ]

    def __init__(self, settings=None):
        super().__init__(settings)
        self.port = int(self.settings.get("port", 5300))
        self._listener = UdpListener(self.port, self._on_packet)
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._prev_accel = None
        self._collision = 0.0
        self.feel = RacingFeel(self.id + ".", self.settings)

    def start(self, ctx) -> None:
        self._listener.start()

    def stop(self, ctx) -> None:
        self._listener.stop()
        super().stop(ctx)

    def status(self) -> str:
        if self._listener.error:
            return self._listener.error
        return super().status()

    def _on_packet(self, data: bytes) -> None:
        d = parse(data)
        if d is None:
            return
        # Collision detection: sudden change in acceleration between packets (60 Hz).
        accel = d["accel"]
        if self._prev_accel is not None:
            jerk = math.dist(accel, self._prev_accel)
            if jerk > 45:
                self._collision = max(self._collision, min((jerk - 45) / 80, 1.0))
        self._prev_accel = accel
        with self._lock:
            self._latest = d
        self.last_data = time.monotonic()
        self.packets += 1

    def telemetry(self) -> CarTelemetry:
        with self._lock:
            d = self._latest
        t = CarTelemetry()
        if d is None or not self.receiving or not d["is_race_on"]:
            return t
        t.active = True
        t.rpm, t.max_rpm, t.idle_rpm = d["rpm"], max(d["max_rpm"], 1000), d["idle_rpm"]
        t.speed, t.throttle, t.brake, t.gear = d["speed"], d["throttle"], d["brake"], d["gear"]
        # Combined slip gives grip usage; slip ratio gives the direction (spin vs lock).
        t.slip = [abs(c) * (1 if r >= 0 else -1) for c, r in zip(d["combined_slip"], d["slip_ratio"])]
        t.rumble_strip = [1.0 if x else 0.0 for x in d["on_rumble"]]
        t.surface = [min(abs(x) / 2.0, 1.0) for x in d["surface_rumble"]]
        t.puddle = [min(max(x, 0.0), 1.0) for x in d["puddle"]]
        t.suspension = d["susp_norm"]
        t.collision, self._collision = self._collision, 0.0
        return t

    def tick(self, ctx, dt: float) -> Outputs:
        return self.feel.update(ctx, self.telemetry(), dt)
