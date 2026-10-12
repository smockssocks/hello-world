import pytest

from ds5forge import triggers as t


def test_feedback_full_strength():
    assert list(t.feedback(0, 8)) == [0x21, 0xFF, 0x03, 0xFF, 0xFF, 0xFF, 0x3F, 0, 0, 0, 0]


def test_weapon_encoding():
    assert list(t.weapon(2, 5, 8)) == [0x25, 0x24, 0x00, 7, 0, 0, 0, 0, 0, 0, 0]


def test_vibration_encoding():
    e = t.vibration(9, 1, 40)
    assert e[0] == 0x26 and e[1:3] == bytes([0x00, 0x02]) and e[9] == 40


def test_zero_strength_is_off():
    assert t.feedback(3, 0) == t.TRIGGER_OFF
    assert t.vibration(0, 5, 0) == t.TRIGGER_OFF


@pytest.mark.parametrize("name", list(t.PRESETS))
def test_presets_build_11_bytes(name):
    assert len(t.PRESETS[name].to_bytes()) == 11


def test_effect_roundtrip_and_scale():
    eff = t.TriggerEffect.from_dict({"type": "feedback", "position": 0, "strength": 8})
    assert eff.to_dict() == {"type": "feedback", "position": 0, "strength": 8}
    half = eff.to_bytes(scale=0.5)
    assert half == t.feedback(0, 4)
    assert eff.to_bytes(scale=0.01) == t.feedback(0, 1)  # never scales a real effect to nothing


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        t.TriggerEffect.from_dict({"type": "laser"})
