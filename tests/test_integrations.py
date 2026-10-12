import struct

from ds5forge.haptics.synth import HapticMixer
from ds5forge.integrations import assetto, f1, forza, outgauge
from ds5forge.integrations.base import CarTelemetry, RacingFeel


class Ctx:
    def __init__(self):
        self.mixer = HapticMixer()


def forza_packet(size=324, rpm=7000.0, max_rpm=8000.0, throttle=255, brake=0, gear=3, rumble=(0, 0, 0, 0),
                 slip=(0.0, 0.0, 0.0, 0.0), ratio=(0.0, 0.0, 0.0, 0.0)):
    sled = [1, 1000, max_rpm, 900.0, rpm] + [0.0] * 12  # through yaw/pitch/roll
    sled += [0.5] * 4 + list(ratio) + [0.0] * 4  # susp, slip ratio, wheel rotation
    sled += list(rumble)
    sled += [0.0] * 4 + [0.0] * 4 + [0.0] * 4 + list(slip) + [0.0] * 4
    sled += [1, 2, 800, 1, 6]
    data = bytearray(forza.SLED.pack(*sled))
    if size == 324:
        data += bytes(12)
    dash = [0.0] * 3 + [30.0, 0.0, 0.0] + [0.0] * 4 + [0.0] * 7 + [1, 1, throttle, brake, 0, 0, gear, 0, 0, 0]
    data += forza.DASH.pack(*dash)
    data += bytes(size - len(data))
    return bytes(data)


def test_forza_horizon_parse():
    d = forza.parse(forza_packet(rumble=(1, 0, 0, 0)))
    assert d["has_dash"] and d["is_race_on"]
    assert d["rpm"] == 7000 and d["throttle"] == 1.0 and d["gear"] == 3 and d["speed"] == 30.0
    assert d["on_rumble"] == [1, 0, 0, 0]


def test_forza_motorsport_sizes():
    for size in (311, 331):
        d = forza.parse(forza_packet(size=size, brake=255))
        assert d["has_dash"] and d["brake"] == 1.0


def test_forza_integration_wheelspin_shakes_throttle():
    integ = forza.ForzaIntegration({"port": 0})
    integ._on_packet(forza_packet(slip=(0, 0, 2.0, 2.0), ratio=(0, 0, 2.0, 2.0)))
    out = integ.tick(Ctx(), 0.01)
    assert out.right_trigger.type == "vibration"
    assert out.left_trigger.type == "slope"
    assert out.lightbar is not None


def test_forza_lockup_shakes_brake():
    integ = forza.ForzaIntegration({"port": 0})
    integ._on_packet(forza_packet(throttle=0, brake=255, slip=(2.0, 2.0, 0, 0), ratio=(-2.0, -2.0, 0, 0)))
    out = integ.tick(Ctx(), 0.01)
    assert out.left_trigger.type == "vibration"


def f1_header(packet_id, fmt=2024, player=0):
    return f1.HEADER.pack(fmt, 24, 1, 0, 1, packet_id, 123, 1.0, 1, 1, player, 255)


def test_f1_car_telemetry():
    car = f1.CAR_TELEMETRY.pack(216, 0.8, 0.0, 0.0, 0, 4, 11000, 0, 90, 0, *[500] * 4, *[90] * 4, *[95] * 4,
                                100, *[23.0] * 4, 0, 0, 1, 1)  # surfaces RL RR FL FR: front wheels on kerb
    data = f1_header(6) + car * 22 + bytes(3)
    integ = f1.F1Integration({"port": 0})
    integ._on_packet(data)
    t = integ._t
    assert t.active and t.gear == 4 and abs(t.speed - 60) < 0.01 and t.rev_lights == 0.9
    assert t.rumble_strip == [1.0, 1.0, 0.0, 0.0]
    integ._on_packet(f1_header(3) + b"COLL" + bytes([0, 5]))
    out = integ.tick(Ctx(), 0.01)
    assert out.player_leds == 0x1F or out.player_leds > 0


def test_f1_ignores_old_format():
    integ = f1.F1Integration({"port": 0})
    integ._on_packet(f1_header(6, fmt=2022) + bytes(1400))
    assert not integ._t.active


def test_outgauge_abs_light():
    pkt = outgauge.OUTGAUGE.pack(0, b"car\0", 0, 3, 0, 20.0, 5000.0, 0, 90, 0.5, 0, 0, 0,
                                 outgauge.DL_ABS, 0.0, 1.0, 0.0) + bytes(36)
    integ = outgauge.OutGaugeIntegration({"port": 0})
    integ._on_packet(pkt)
    out = integ.tick(Ctx(), 0.01)
    assert out.left_trigger.type == "vibration"


def test_assetto_parse_and_activity():
    buf = bytearray(800)
    struct.pack_into("<iff", buf, 0, 42, 1.0, 0.0)
    struct.pack_into("<ii", buf, 16, 4, 6500)
    struct.pack_into("<f", buf, 28, 120.0)
    struct.pack_into("<4f", buf, 56, 0.1, 0.1, 2.5, 2.5)
    struct.pack_into("<i", buf, assetto.OFF_MAX_RPM, 7500)
    struct.pack_into("<4f", buf, assetto.OFF_KERB_VIB, 0.4, 0.0, 0.0, 0.0)
    d = assetto.parse(bytes(buf))
    assert d["gear"] == 3 and d["max_rpm"] == 7500 and abs(d["vibrations"][0] - 0.4) < 1e-6
    integ = assetto.AssettoIntegration()
    t = integ.to_telemetry(d)
    assert t.active and abs(t.rumble_strip[0] - 0.8) < 1e-6 and t.slip[2] > 1


def test_racing_feel_inactive_releases_triggers():
    feel = RacingFeel("x.", {})
    out = feel.update(Ctx(), CarTelemetry(active=False), 0.01)
    assert out.left_trigger.type == "off" and out.right_trigger.type == "off"
