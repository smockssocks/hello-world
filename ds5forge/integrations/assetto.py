"""Assetto Corsa and Assetto Corsa Competizione via shared memory (no game setup needed)."""

from __future__ import annotations

import logging
import struct
import sys
import time

from .base import CarTelemetry, Integration, Outputs, RacingFeel

log = logging.getLogger(__name__)

PHYSICS_TAG = "Local\\acpmf_physics"
MAP_SIZES = (800, 720, 256)  # try the full ACC layout first, then shorter AC layouts

OFF_PACKET_ID = 0
OFF_GAS = 4
OFF_BRAKE = 8
OFF_GEAR = 16
OFF_RPM = 20
OFF_SPEED_KMH = 28
OFF_WHEEL_SLIP = 56
OFF_SUSPENSION = 184
OFF_TYRES_OUT = 244
OFF_MAX_RPM = 588
OFF_KERB_VIB = 784  # kerbVibration, slipVibrations, gVibrations, absVibrations

# AC's wheelSlip is roughly "combined slip"; past ~1 the tyre is sliding.
AC_SLIP_SCALE = 1.0


def parse(buf: bytes) -> dict:
    d = {
        "packet_id": struct.unpack_from("<i", buf, OFF_PACKET_ID)[0],
        "gas": struct.unpack_from("<f", buf, OFF_GAS)[0],
        "brake": struct.unpack_from("<f", buf, OFF_BRAKE)[0],
        "gear": struct.unpack_from("<i", buf, OFF_GEAR)[0] - 1,  # 0=R,1=N in AC -> -1/0
        "rpm": struct.unpack_from("<i", buf, OFF_RPM)[0],
        "speed_kmh": struct.unpack_from("<f", buf, OFF_SPEED_KMH)[0],
        "wheel_slip": list(struct.unpack_from("<4f", buf, OFF_WHEEL_SLIP)),
        "suspension": list(struct.unpack_from("<4f", buf, OFF_SUSPENSION)),
        "tyres_out": struct.unpack_from("<i", buf, OFF_TYRES_OUT)[0],
        "max_rpm": None, "vibrations": None,
    }
    if len(buf) >= OFF_MAX_RPM + 4:
        mr = struct.unpack_from("<i", buf, OFF_MAX_RPM)[0]
        if 1500 < mr < 25000:
            d["max_rpm"] = mr
    if len(buf) >= OFF_KERB_VIB + 16:
        vib = struct.unpack_from("<4f", buf, OFF_KERB_VIB)
        if all(0.0 <= x <= 5.0 for x in vib):
            d["vibrations"] = vib
    return d


class AssettoIntegration(Integration):
    id = "assetto"
    name = "Assetto Corsa / ACC"
    games = "Assetto Corsa, Assetto Corsa Competizione"
    setup = ["Nothing to set up - just start a session. DS5 Forge reads the game's shared memory."]

    def __init__(self, settings=None):
        super().__init__(settings)
        self._mm = None
        self._last_packet = None
        self._last_open_try = 0.0
        self._max_rpm_seen = 7000.0
        self._susp_max = 0.15
        self.feel = RacingFeel(self.id + ".", self.settings)

    def _open(self) -> None:
        if sys.platform != "win32" or time.monotonic() - self._last_open_try < 2.0:
            return
        self._last_open_try = time.monotonic()
        import mmap
        for size in MAP_SIZES:
            try:
                self._mm = mmap.mmap(-1, size, tagname=PHYSICS_TAG, access=mmap.ACCESS_READ)
                return
            except (OSError, ValueError):
                continue

    def stop(self, ctx) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        super().stop(ctx)

    def read(self) -> dict | None:
        if self._mm is None:
            self._open()
            if self._mm is None:
                return None
        self._mm.seek(0)
        return parse(self._mm.read(len(self._mm)))

    def to_telemetry(self, d: dict | None) -> CarTelemetry:
        t = CarTelemetry()
        if d is None:
            return t
        if d["packet_id"] != self._last_packet:
            self._last_packet = d["packet_id"]
            self.last_data = time.monotonic()
            self.packets += 1
        if not self.receiving:
            return t
        t.active = True
        t.throttle, t.brake, t.gear = d["gas"], d["brake"], d["gear"]
        t.rpm, t.speed = d["rpm"], d["speed_kmh"] / 3.6
        self._max_rpm_seen = max(self._max_rpm_seen, t.rpm)
        t.max_rpm = d["max_rpm"] or self._max_rpm_seen
        # AC doesn't say spin vs lock: infer from pedals.
        sign = -1.0 if t.brake > t.throttle else 1.0
        t.slip = [sign * s / AC_SLIP_SCALE for s in d["wheel_slip"]]
        self._susp_max = max(self._susp_max, *d["suspension"])
        t.suspension = [min(max(s / self._susp_max, 0.0), 1.0) for s in d["suspension"]]
        rough = 0.6 if d["tyres_out"] > 0 else 0.0
        t.surface = [rough] * 4
        vib = d["vibrations"]
        if vib is not None:
            kerb, _slip, g, abs_vib = vib
            t.rumble_strip = [min(kerb * 2, 1.0)] * 4
            t.abs_active = abs_vib > 0.02
            if g > 0.6:
                t.collision = min(g - 0.6, 1.0)
        return t

    def tick(self, ctx, dt: float) -> Outputs:
        return self.feel.update(ctx, self.to_telemetry(self.read()), dt)
