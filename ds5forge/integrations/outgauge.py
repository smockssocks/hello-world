"""OutGauge UDP (BeamNG.drive, Live for Speed and other games that speak it)."""

from __future__ import annotations

import struct
import threading
import time

from .base import CarTelemetry, Integration, Outputs, RacingFeel, UdpListener

OUTGAUGE = struct.Struct("<I4sHBBfffffffIIfff")  # 60 bytes before the display strings

DL_SHIFT = 1 << 0
DL_TC = 1 << 4
DL_ABS = 1 << 10


def parse(data: bytes) -> dict | None:
    if len(data) < OUTGAUGE.size:
        return None
    v = OUTGAUGE.unpack_from(data, 0)
    return {
        "gear": v[3] - 1,  # 0=R, 1=N, 2=1st -> -1, 0, 1
        "speed": v[5], "rpm": v[6],
        "show_lights": v[13],
        "throttle": v[14], "brake": v[15],
    }


class OutGaugeIntegration(Integration):
    id = "outgauge"
    name = "OutGauge (BeamNG.drive, Live for Speed)"
    games = "BeamNG.drive, Live for Speed"
    setup = [
        "BeamNG.drive: Options > Other > enable 'OutGauge support', IP 127.0.0.1, port 4444.",
        "Live for Speed: in cfg.txt set 'OutGauge Mode 1', 'OutGauge IP 127.0.0.1', 'OutGauge Port 4444'.",
    ]

    def __init__(self, settings=None):
        super().__init__(settings)
        self.port = int(self.settings.get("port", 4444))
        self._listener = UdpListener(self.port, self._on_packet)
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._max_rpm = 6000.0
        self.feel = RacingFeel(self.id + ".", self.settings)

    def start(self, ctx) -> None:
        self._listener.start()

    def stop(self, ctx) -> None:
        self._listener.stop()
        super().stop(ctx)

    def status(self) -> str:
        return self._listener.error or super().status()

    def _on_packet(self, data: bytes) -> None:
        d = parse(data)
        if d is None:
            return
        with self._lock:
            self._latest = d
        self.last_data = time.monotonic()
        self.packets += 1

    def tick(self, ctx, dt: float) -> Outputs:
        with self._lock:
            d = self._latest
        t = CarTelemetry()
        if d is not None and self.receiving:
            t.active = True
            t.speed, t.rpm, t.gear = d["speed"], d["rpm"], d["gear"]
            t.throttle, t.brake = d["throttle"], d["brake"]
            self._max_rpm = max(self._max_rpm, t.rpm)
            t.max_rpm = self._max_rpm
            lights = d["show_lights"]
            t.abs_active = bool(lights & DL_ABS)
            t.tc_active = bool(lights & DL_TC)
            if lights & DL_SHIFT:
                t.rev_lights = 1.0
        return self.feel.update(ctx, t, dt)
