"""EA SPORTS F1 23 / 24 / 25 via the official UDP telemetry."""

from __future__ import annotations

import struct
import threading
import time

from .base import CarTelemetry, Integration, Outputs, RacingFeel, UdpListener

HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 bytes (F1 23 and later)
CAR_TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")  # 60 bytes per car
CAR_STATUS_PREFIX = struct.Struct("<BBBBBfffHH")  # first 21 bytes of car status per car
CAR_STATUS_SIZE = {2023: 47, 2024: 55, 2025: 55}
NUM_CARS = 22

PACKET_EVENT = 3
PACKET_CAR_TELEMETRY = 6
PACKET_CAR_STATUS = 7
PACKET_MOTION_EX = 13

# Surface types from the spec.
SURFACE_RUMBLE_STRIP = {1: 1.0, 11: 0.8}  # rumble strip, ridged
SURFACE_ROUGH = {2: 0.1, 3: 0.8, 4: 0.7, 5: 0.6, 6: 0.6, 7: 0.5, 8: 0.3, 9: 0.5, 10: 0.2}

# F1 orders wheels RL, RR, FL, FR; we use FL, FR, RL, RR.
F1_TO_FLFR = (2, 3, 0, 1)

# Slip ratio at which a tyre is past peak grip.
F1_PEAK_SLIP = 0.12


def _reorder(values):
    return [values[i] for i in F1_TO_FLFR]


class F1Integration(Integration):
    id = "f1"
    name = "EA SPORTS F1 (23 / 24 / 25)"
    games = "F1 23, F1 24, F1 25"
    setup = [
        "In the game open Settings > Telemetry Settings.",
        "UDP Telemetry: On, UDP Broadcast Mode: Off, UDP IP Address: 127.0.0.1, UDP Port: 20777.",
        "UDP Send Rate: 60Hz (or higher), UDP Format: the game's own year (e.g. 2024).",
    ]

    def __init__(self, settings=None):
        super().__init__(settings)
        self.port = int(self.settings.get("port", 20777))
        self._listener = UdpListener(self.port, self._on_packet)
        self._lock = threading.Lock()
        self._t = CarTelemetry()
        self._collision = 0.0
        self._events: list[str] = []
        self.feel = RacingFeel(self.id + ".", self.settings)

    def start(self, ctx) -> None:
        self._listener.start()

    def stop(self, ctx) -> None:
        self._listener.stop()
        super().stop(ctx)

    def status(self) -> str:
        return self._listener.error or super().status()

    def _on_packet(self, data: bytes) -> None:
        if len(data) < HEADER.size:
            return
        h = HEADER.unpack_from(data, 0)
        fmt, packet_id, player = h[0], h[5], h[10]
        if fmt < 2023 or player >= NUM_CARS:
            return
        body = HEADER.size
        with self._lock:
            t = self._t
            if packet_id == PACKET_CAR_TELEMETRY:
                off = body + player * CAR_TELEMETRY.size
                if len(data) < off + CAR_TELEMETRY.size:
                    return
                v = CAR_TELEMETRY.unpack_from(data, off)
                t.speed = v[0] / 3.6
                t.throttle, t.brake = v[1], v[3]
                t.gear, t.rpm = v[5], v[6]
                t.rev_lights = v[8] / 100.0
                surfaces = _reorder(v[-4:])
                t.rumble_strip = [SURFACE_RUMBLE_STRIP.get(s, 0.0) for s in surfaces]
                t.surface = [SURFACE_ROUGH.get(s, 0.0) for s in surfaces]
                t.active = True
                self.last_data = time.monotonic()
                self.packets += 1
            elif packet_id == PACKET_MOTION_EX:
                if len(data) < body + 4 * 4 * 5:
                    return
                floats = struct.unpack_from("<20f", data, body)
                t.suspension = [min(max(abs(x) / 100.0, 0.0), 1.0) for x in _reorder(floats[0:4])]
                t.slip = [x / F1_PEAK_SLIP for x in _reorder(floats[16:20])]
            elif packet_id == PACKET_CAR_STATUS:
                size = CAR_STATUS_SIZE.get(fmt, 55)
                off = body + player * size
                if len(data) >= off + CAR_STATUS_PREFIX.size:
                    v = CAR_STATUS_PREFIX.unpack_from(data, off)
                    if 3000 < v[8] < 25000:
                        t.max_rpm = v[8]
                    if 0 < v[9] < v[8]:
                        t.idle_rpm = v[9]
            elif packet_id == PACKET_EVENT and len(data) >= body + 4:
                code = data[body:body + 4]
                if code == b"COLL" and len(data) >= body + 6:
                    if player in (data[body + 4], data[body + 5]):
                        self._collision = 1.0
                elif code == b"LGOT":
                    self._events.append("lights_out")

    def tick(self, ctx, dt: float) -> Outputs:
        with self._lock:
            t = CarTelemetry(**vars(self._t))
            t.slip, t.rumble_strip, t.surface = list(t.slip), list(t.rumble_strip), list(t.surface)
            t.collision, self._collision = self._collision, 0.0
            t.events, self._events = self._events, []
        t.active = t.active and self.receiving
        return self.feel.update(ctx, t, dt)
