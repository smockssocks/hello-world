"""Adaptive trigger effects.

Effect encodings follow Nielk1's documented DualSense trigger modes
(the same modes games use on PS5). Positions are in tenths of trigger travel
(0 = released, 9 = fully pulled); strengths are 1-8 (0 means "no effect").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .protocol import TRIGGER_OFF

MODE_OFF = 0x05
MODE_FEEDBACK = 0x21
MODE_BOW = 0x22
MODE_GALLOPING = 0x23
MODE_WEAPON = 0x25
MODE_VIBRATION = 0x26
MODE_MACHINE = 0x27


def _clamp(v: float, lo: int, hi: int) -> int:
    return int(min(max(round(v), lo), hi))


def _zones_pack(strengths: list[int]) -> tuple[int, int]:
    """Pack 10 per-zone strengths (0-8) into (active_zone_mask, 3-bit strength field)."""
    active = 0
    forces = 0
    for i, s in enumerate(strengths[:10]):
        if s > 0:
            active |= 1 << i
            forces |= ((s - 1) & 0x07) << (3 * i)
    return active, forces


def _zone_effect(mode: int, strengths: list[int], frequency: int = 0) -> bytes:
    active, forces = _zones_pack(strengths)
    if not active:
        return TRIGGER_OFF
    out = bytearray(11)
    out[0] = mode
    out[1:3] = active.to_bytes(2, "little")
    out[3:7] = forces.to_bytes(4, "little")
    out[9] = frequency & 0xFF
    return bytes(out)


def off() -> bytes:
    return TRIGGER_OFF


def feedback(position: int, strength: int) -> bytes:
    """Constant resistance from ``position`` to the end of travel."""
    position = _clamp(position, 0, 9)
    strength = _clamp(strength, 0, 8)
    return _zone_effect(MODE_FEEDBACK, [0] * position + [strength] * (10 - position))


def multi_feedback(strengths: list[float]) -> bytes:
    """Per-zone resistance, 10 values 0-8."""
    return _zone_effect(MODE_FEEDBACK, [_clamp(s, 0, 8) for s in strengths])


def slope_feedback(start: int, end: int, start_strength: int, end_strength: int) -> bytes:
    """Resistance that ramps from ``start_strength`` at ``start`` to ``end_strength`` at ``end``."""
    start = _clamp(start, 0, 8)
    end = _clamp(end, start + 1, 9)
    strengths = [0] * 10
    for i in range(start, 10):
        if i >= end:
            strengths[i] = end_strength
        else:
            t = (i - start) / (end - start)
            strengths[i] = start_strength + (end_strength - start_strength) * t
    return multi_feedback(strengths)


def weapon(start: int, end: int, strength: int) -> bytes:
    """Resistance that "breaks" like a gun trigger between ``start`` and ``end``."""
    start = _clamp(start, 2, 7)
    end = _clamp(end, start + 1, 8)
    strength = _clamp(strength, 0, 8)
    if strength == 0:
        return TRIGGER_OFF
    zones = (1 << start) | (1 << end)
    out = bytearray(11)
    out[0] = MODE_WEAPON
    out[1:3] = zones.to_bytes(2, "little")
    out[3] = strength - 1
    return bytes(out)


def vibration(position: int, amplitude: int, frequency: int) -> bytes:
    """Trigger vibrates (once pulled past ``position``) at ``frequency`` Hz."""
    position = _clamp(position, 0, 9)
    amplitude = _clamp(amplitude, 0, 8)
    frequency = _clamp(frequency, 0, 255)
    if amplitude == 0 or frequency == 0:
        return TRIGGER_OFF
    return _zone_effect(MODE_VIBRATION, [0] * position + [amplitude] * (10 - position), frequency)


def multi_vibration(frequency: int, amplitudes: list[float]) -> bytes:
    frequency = _clamp(frequency, 0, 255)
    if frequency == 0:
        return TRIGGER_OFF
    return _zone_effect(MODE_VIBRATION, [_clamp(a, 0, 8) for a in amplitudes], frequency)


def bow(start: int, end: int, strength: int, snap: int) -> bytes:
    """Tension that builds then snaps back when released, like drawing a bow."""
    start = _clamp(start, 0, 8)
    end = _clamp(end, start + 1, 8)
    strength = _clamp(strength, 0, 8)
    snap = _clamp(snap, 0, 8)
    if strength == 0 or snap == 0:
        return TRIGGER_OFF
    zones = (1 << start) | (1 << end)
    pair = ((strength - 1) & 0x07) | (((snap - 1) & 0x07) << 3)
    out = bytearray(11)
    out[0] = MODE_BOW
    out[1:3] = zones.to_bytes(2, "little")
    out[3:5] = pair.to_bytes(2, "little")
    return bytes(out)


def galloping(start: int, end: int, first_foot: int, second_foot: int, frequency: int) -> bytes:
    """Rhythmic two-beat pulse, like a horse's gallop."""
    start = _clamp(start, 0, 8)
    end = _clamp(end, start + 1, 9)
    first_foot = _clamp(first_foot, 0, 6)
    second_foot = _clamp(second_foot, first_foot + 1, 7)
    frequency = _clamp(frequency, 0, 255)
    if frequency == 0:
        return TRIGGER_OFF
    zones = (1 << start) | (1 << end)
    ratio = (second_foot & 0x07) | ((first_foot & 0x07) << 3)
    out = bytearray(11)
    out[0] = MODE_GALLOPING
    out[1:3] = zones.to_bytes(2, "little")
    out[3] = ratio
    out[4] = frequency
    return bytes(out)


def machine(start: int, end: int, strength_a: int, strength_b: int, frequency: int, period: int) -> bytes:
    """Vibration alternating between two strengths - engines, machine guns."""
    start = _clamp(start, 0, 8)
    end = _clamp(end, start + 1, 9)
    strength_a = _clamp(strength_a, 0, 7)
    strength_b = _clamp(strength_b, 0, 7)
    frequency = _clamp(frequency, 0, 255)
    period = _clamp(period, 0, 255)
    if frequency == 0:
        return TRIGGER_OFF
    zones = (1 << start) | (1 << end)
    out = bytearray(11)
    out[0] = MODE_MACHINE
    out[1:3] = zones.to_bytes(2, "little")
    out[3] = (strength_a & 0x07) | ((strength_b & 0x07) << 3)
    out[4] = frequency
    out[5] = period
    return bytes(out)


# --------------------------------------------------------------- declarative form

# Parameters that represent force/amplitude and get scaled by the user's trigger
# intensity setting.
_STRENGTH_PARAMS = {"strength", "amplitude", "start_strength", "end_strength",
                    "strength_a", "strength_b", "snap"}

_BUILDERS = {
    "off": (off, ()),
    "feedback": (feedback, ("position", "strength")),
    "slope": (slope_feedback, ("start", "end", "start_strength", "end_strength")),
    "weapon": (weapon, ("start", "end", "strength")),
    "vibration": (vibration, ("position", "amplitude", "frequency")),
    "bow": (bow, ("start", "end", "strength", "snap")),
    "galloping": (galloping, ("start", "end", "first_foot", "second_foot", "frequency")),
    "machine": (machine, ("start", "end", "strength_a", "strength_b", "frequency", "period")),
    "multi_feedback": (multi_feedback, ("strengths",)),
    "multi_vibration": (multi_vibration, ("frequency", "amplitudes")),
}

EFFECT_TYPES = tuple(_BUILDERS)

PARAM_RANGES = {
    "position": (0, 9), "start": (0, 9), "end": (1, 9),
    "strength": (0, 8), "amplitude": (0, 8), "start_strength": (0, 8), "end_strength": (0, 8),
    "snap": (0, 8), "strength_a": (0, 7), "strength_b": (0, 7),
    "first_foot": (0, 6), "second_foot": (1, 7),
    "frequency": (0, 255), "period": (0, 255),
}


@dataclass(frozen=True)
class TriggerEffect:
    """A trigger effect described by type + parameters (serialisable to JSON)."""

    type: str = "off"
    params: dict[str, Any] = field(default_factory=dict)

    def to_bytes(self, scale: float = 1.0) -> bytes:
        builder, names = _BUILDERS.get(self.type, _BUILDERS["off"])
        args = []
        for name in names:
            v = self.params.get(name, _DEFAULTS.get(name, 0))
            if scale != 1.0:
                if name in _STRENGTH_PARAMS:
                    v = v * scale if v == 0 else max(1, v * scale)
                elif name in ("strengths", "amplitudes"):
                    v = [s * scale if s == 0 else max(1, s * scale) for s in v]
            args.append(v)
        return builder(*args)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.params}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TriggerEffect":
        if not d:
            return cls()
        d = dict(d)
        kind = d.pop("type", "off")
        if kind not in _BUILDERS:
            raise ValueError("unknown trigger effect type %r" % kind)
        return cls(kind, d)

    def __hash__(self) -> int:  # params dict isn't hashable by default
        return hash((self.type, tuple(sorted((k, str(v)) for k, v in self.params.items()))))


_DEFAULTS = {
    "position": 0, "start": 2, "end": 6, "strength": 5, "amplitude": 5,
    "start_strength": 1, "end_strength": 8, "snap": 5, "strength_a": 3, "strength_b": 6,
    "first_foot": 2, "second_foot": 5, "frequency": 30, "period": 10,
    "strengths": [0] * 10, "amplitudes": [0] * 10,
}

OFF = TriggerEffect()

PRESETS: dict[str, TriggerEffect] = {
    "Off": OFF,
    "Light resistance": TriggerEffect("feedback", {"position": 1, "strength": 2}),
    "Heavy resistance": TriggerEffect("feedback", {"position": 0, "strength": 7}),
    "Brake pedal": TriggerEffect("slope", {"start": 1, "end": 8, "start_strength": 1, "end_strength": 8}),
    "Accelerator": TriggerEffect("slope", {"start": 0, "end": 9, "start_strength": 1, "end_strength": 3}),
    "Pistol": TriggerEffect("weapon", {"start": 3, "end": 5, "strength": 7}),
    "Heavy gun": TriggerEffect("weapon", {"start": 2, "end": 6, "strength": 8}),
    "Bow": TriggerEffect("bow", {"start": 1, "end": 6, "strength": 6, "snap": 7}),
    "Machine gun": TriggerEffect("vibration", {"position": 2, "amplitude": 8, "frequency": 12}),
    "Machinery": TriggerEffect("machine", {"start": 1, "end": 9, "strength_a": 3, "strength_b": 7,
                                           "frequency": 6, "period": 12}),
    "Gallop": TriggerEffect("galloping", {"start": 0, "end": 9, "first_foot": 2, "second_foot": 5,
                                          "frequency": 3}),
    "Engine idle": TriggerEffect("vibration", {"position": 0, "amplitude": 2, "frequency": 25}),
    "Hard stop at half": TriggerEffect("multi_feedback", {"strengths": [0, 0, 0, 0, 8, 8, 8, 8, 8, 8]}),
}
