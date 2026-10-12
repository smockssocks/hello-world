import struct

from ds5forge import protocol as p


def saxense_crc(data: bytes) -> int:
    """Bit-by-bit CRC from the SAxense reference implementation."""
    crc = (~0xEADA2D49) & 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1) & 0xFFFFFFFF)
    return (~crc) & 0xFFFFFFFF


def test_crc_matches_reference_seed():
    data = bytes(range(60))
    assert p.bt_crc32(data) == saxense_crc(data)


def test_usb_report_layout():
    st = p.OutputState(rumble_left=200, rumble_right=50, lightbar=(1, 2, 3), player_leds=0b00101)
    st.right_trigger = bytes([0x21] + [9] * 10)
    r = p.build_usb_report(st, p.UpdateMask.all())
    assert len(r) == p.USB_OUTPUT_REPORT_SIZE
    assert r[0] == 0x02
    c = r[1:]
    assert c[0] & 0x0F == 0x0F  # rumble + both triggers
    assert c[p.OFF_MOTOR_LEFT] == 200 and c[p.OFF_MOTOR_RIGHT] == 50
    assert c[p.OFF_RIGHT_TRIGGER] == 0x21
    assert c[p.OFF_LEFT_TRIGGER] == 0x05
    assert tuple(c[44:47]) == (1, 2, 3)
    assert c[p.OFF_PLAYER_LEDS] == 0b00101 | p.PLAYER_LEDS_INSTANT


def test_mask_limits_what_is_sent():
    st = p.OutputState(rumble_left=255)
    c = p.build_usb_report(st, p.UpdateMask(lightbar=True))[1:]
    assert c[0] == 0 and c[p.OFF_MOTOR_LEFT] == 0
    assert c[1] == p.FLAG1_LIGHTBAR


def test_bt_report_has_valid_crc_and_seq():
    r = p.build_bt_report(p.OutputState(), p.UpdateMask.all(), seq=5)
    assert len(r) == 78 and r[0] == 0x31 and r[1] == 0x50 and r[2] == 0x10
    assert struct.unpack("<I", r[-4:])[0] == saxense_crc(r[:-4])


def test_bt_haptics_report_matches_saxense_layout():
    samples = bytes(range(64))
    r = p.build_bt_haptics_report(samples, counter=7)
    assert len(r) == 141
    assert r[0] == 0x32 and r[1] == 0
    assert list(r[2:11]) == [0x91, 7, 0xFE, 0, 0, 0, 0, 0xFF, 7]
    assert r[11] == 0x92 and r[12] == 64
    assert r[13:77] == samples
    assert struct.unpack("<I", r[-4:])[0] == saxense_crc(r[:-4])


def _usb_input(**kw):
    c = bytearray(63)
    c[0:6] = bytes([kw.get("lx", 128), 128, 128, 128, kw.get("l2", 0), kw.get("r2", 0)])
    c[7] = kw.get("b0", 0x08)
    c[8] = kw.get("b1", 0)
    c[9] = kw.get("b2", 0)
    c[52] = kw.get("status", 0x05)
    return bytes([0x01]) + bytes(c)


def test_parse_usb_input():
    s = p.parse_input(_usb_input(l2=10, r2=250, b0=0x20 | 0x00, b1=0x04, status=0x17))
    assert s.valid and s.l2 == 10 and s.r2 == 250
    assert s.buttons["cross"] and s.buttons["l2"]
    assert s.dpad == 0 and s.pressed("up")
    assert s.battery == 75 and s.charging


def test_parse_bt_input_and_reject_simple_report():
    usb = _usb_input(lx=3)
    bt = bytes([0x31, 0x00]) + usb[1:] + bytes(78 - 64 - 1)
    s = p.parse_input(bt)
    assert s.lx == 3
    assert p.parse_input(bytes([0x01] + [0] * 9)) is None
