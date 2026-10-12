"""DualSense HID protocol: output report building, input report parsing, CRC.

Layouts follow the Linux ``hid-playstation`` driver. The Bluetooth audio-haptics
report (0x32) follows the reverse engineering done by the SAxense project
(https://github.com/egormanga/SAxense); this is an independent implementation.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

SONY_VID = 0x054C
DUALSENSE_PID = 0x0CE6
DUALSENSE_EDGE_PID = 0x0DF2
SUPPORTED_PIDS = (DUALSENSE_PID, DUALSENSE_EDGE_PID)

USB_INPUT_REPORT_ID = 0x01
USB_OUTPUT_REPORT_ID = 0x02
USB_OUTPUT_REPORT_SIZE = 48
BT_INPUT_REPORT_ID = 0x31
BT_OUTPUT_REPORT_ID = 0x31
BT_OUTPUT_REPORT_SIZE = 78
BT_OUTPUT_TAG = 0x10
BT_CRC_SEED = 0xA2  # "output report" header byte that is hashed but never sent
CALIBRATION_FEATURE_REPORT_ID = 0x05
CALIBRATION_FEATURE_REPORT_SIZE = 41

# Bluetooth audio haptics: stereo, signed 8-bit, 3 kHz, 32 frames per report.
BT_HAPTICS_REPORT_ID = 0x32
BT_HAPTICS_REPORT_SIZE = 141
BT_HAPTICS_SAMPLE_RATE = 3000
BT_HAPTICS_FRAMES = 32
BT_HAPTICS_PERIOD = BT_HAPTICS_FRAMES / BT_HAPTICS_SAMPLE_RATE  # ~10.67 ms

COMMON_SIZE = 47
TRIGGER_EFFECT_SIZE = 11

# valid_flag0
FLAG0_COMPATIBLE_VIBRATION = 0x01
FLAG0_HAPTICS_SELECT = 0x02
FLAG0_RIGHT_TRIGGER = 0x04
FLAG0_LEFT_TRIGGER = 0x08
# valid_flag1
FLAG1_MIC_MUTE_LED = 0x01
FLAG1_POWER_SAVE = 0x02
FLAG1_LIGHTBAR = 0x04
FLAG1_RELEASE_LEDS = 0x08
FLAG1_PLAYER_LEDS = 0x10
# valid_flag2
FLAG2_LED_BRIGHTNESS = 0x01
FLAG2_LIGHTBAR_SETUP = 0x02
FLAG2_COMPATIBLE_VIBRATION2 = 0x04

LIGHTBAR_SETUP_LIGHT_OUT = 0x02
PLAYER_LEDS_INSTANT = 0x20

# Offsets inside the 47-byte common output block.
OFF_FLAG0 = 0
OFF_FLAG1 = 1
OFF_MOTOR_RIGHT = 2
OFF_MOTOR_LEFT = 3
OFF_MIC_LED = 8
OFF_RIGHT_TRIGGER = 10
OFF_LEFT_TRIGGER = 21
OFF_FLAG2 = 38
OFF_LIGHTBAR_SETUP = 41
OFF_LED_BRIGHTNESS = 42
OFF_PLAYER_LEDS = 43
OFF_LIGHTBAR_RGB = 44

TRIGGER_OFF = bytes([0x05] + [0] * 10)


def bt_crc32(data: bytes) -> int:
    """CRC32 used by DualSense Bluetooth reports (seeded with the 0xA2 header byte)."""
    return zlib.crc32(data, zlib.crc32(bytes([BT_CRC_SEED]))) & 0xFFFFFFFF


@dataclass
class OutputState:
    """Everything we can tell the controller. ``changed`` flags decide what is sent."""

    rumble_left: int = 0  # strong / low frequency motor, 0-255
    rumble_right: int = 0  # weak / high frequency motor, 0-255
    right_trigger: bytes = TRIGGER_OFF
    left_trigger: bytes = TRIGGER_OFF
    lightbar: tuple[int, int, int] = (0, 0, 64)
    player_leds: int = 0
    mic_led: int = 0  # 0 off, 1 on, 2 pulse
    led_brightness: int = 0  # 0 bright, 1 mid, 2 dim


@dataclass
class UpdateMask:
    rumble: bool = False
    right_trigger: bool = False
    left_trigger: bool = False
    lightbar: bool = False
    player_leds: bool = False
    mic_led: bool = False
    led_brightness: bool = False
    release_lightbar: bool = False

    def any(self) -> bool:
        return any(vars(self).values())

    @classmethod
    def all(cls) -> "UpdateMask":
        return cls(True, True, True, True, True, True, True, False)


def build_common(state: OutputState, mask: UpdateMask) -> bytearray:
    c = bytearray(COMMON_SIZE)
    if mask.rumble:
        # Rumble emulation. Older firmware uses flag0 bit0, firmware >= 2.24 uses
        # flag2 bit2; setting both works on either.
        c[OFF_FLAG0] |= FLAG0_COMPATIBLE_VIBRATION | FLAG0_HAPTICS_SELECT
        c[OFF_FLAG2] |= FLAG2_COMPATIBLE_VIBRATION2
        c[OFF_MOTOR_LEFT] = _u8(state.rumble_left)
        c[OFF_MOTOR_RIGHT] = _u8(state.rumble_right)
    if mask.right_trigger:
        c[OFF_FLAG0] |= FLAG0_RIGHT_TRIGGER
        c[OFF_RIGHT_TRIGGER:OFF_RIGHT_TRIGGER + TRIGGER_EFFECT_SIZE] = _effect(state.right_trigger)
    if mask.left_trigger:
        c[OFF_FLAG0] |= FLAG0_LEFT_TRIGGER
        c[OFF_LEFT_TRIGGER:OFF_LEFT_TRIGGER + TRIGGER_EFFECT_SIZE] = _effect(state.left_trigger)
    if mask.mic_led:
        c[OFF_FLAG1] |= FLAG1_MIC_MUTE_LED
        c[OFF_MIC_LED] = state.mic_led & 0x03
    if mask.lightbar:
        c[OFF_FLAG1] |= FLAG1_LIGHTBAR
        r, g, b = state.lightbar
        c[OFF_LIGHTBAR_RGB:OFF_LIGHTBAR_RGB + 3] = bytes((_u8(r), _u8(g), _u8(b)))
    if mask.player_leds:
        c[OFF_FLAG1] |= FLAG1_PLAYER_LEDS
        c[OFF_PLAYER_LEDS] = (state.player_leds & 0x1F) | PLAYER_LEDS_INSTANT
    if mask.led_brightness:
        c[OFF_FLAG2] |= FLAG2_LED_BRIGHTNESS
        c[OFF_LED_BRIGHTNESS] = min(max(state.led_brightness, 0), 2)
    if mask.release_lightbar:
        # Stop the firmware's blue "connecting" animation so our colour sticks.
        c[OFF_FLAG2] |= FLAG2_LIGHTBAR_SETUP
        c[OFF_LIGHTBAR_SETUP] = LIGHTBAR_SETUP_LIGHT_OUT
    return c


def build_usb_report(state: OutputState, mask: UpdateMask) -> bytes:
    return bytes([USB_OUTPUT_REPORT_ID]) + bytes(build_common(state, mask))


def build_bt_report(state: OutputState, mask: UpdateMask, seq: int) -> bytes:
    r = bytearray(BT_OUTPUT_REPORT_SIZE)
    r[0] = BT_OUTPUT_REPORT_ID
    r[1] = (seq & 0x0F) << 4
    r[2] = BT_OUTPUT_TAG
    r[3:3 + COMMON_SIZE] = build_common(state, mask)
    _put_crc(r)
    return bytes(r)


def build_bt_haptics_report(samples: bytes, counter: int) -> bytes:
    """Bluetooth audio-haptics report.

    ``samples`` is 64 bytes: 32 interleaved stereo frames of signed 8-bit PCM at
    3 kHz (left actuator first). The report contains two sub-packets:
    0x11 (control, carries a running counter) and 0x12 (the samples).
    """
    if len(samples) != BT_HAPTICS_FRAMES * 2:
        raise ValueError("expected %d sample bytes" % (BT_HAPTICS_FRAMES * 2))
    r = bytearray(BT_HAPTICS_REPORT_SIZE)
    r[0] = BT_HAPTICS_REPORT_ID
    r[1] = 0  # tag 0, seq 0
    pos = 2
    # Sub-packet header byte: pid in bits 0-5, "sized" flag in bit 7.
    r[pos:pos + 9] = bytes([0x80 | 0x11, 7, 0xFE, 0, 0, 0, 0, 0xFF, counter & 0xFF])
    pos += 9
    r[pos:pos + 2] = bytes([0x80 | 0x12, len(samples)])
    pos += 2
    r[pos:pos + len(samples)] = samples
    _put_crc(r)
    return bytes(r)


def _put_crc(r: bytearray) -> None:
    crc = bt_crc32(bytes(r[:-4]))
    r[-4:] = crc.to_bytes(4, "little")


def _u8(v: float) -> int:
    return int(min(max(v, 0), 255))


def _effect(effect: bytes) -> bytes:
    effect = bytes(effect)
    if len(effect) != TRIGGER_EFFECT_SIZE:
        raise ValueError("trigger effect must be 11 bytes")
    return effect


# --------------------------------------------------------------------------- input

BUTTON_NAMES = (
    "square", "cross", "circle", "triangle",
    "l1", "r1", "l2", "r2", "create", "options", "l3", "r3",
    "ps", "touchpad", "mute",
)


@dataclass
class InputState:
    lx: int = 128
    ly: int = 128
    rx: int = 128
    ry: int = 128
    l2: int = 0
    r2: int = 0
    dpad: int = 8  # 0=up, clockwise to 7=up-left, 8=released
    buttons: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(BUTTON_NAMES, False))
    battery: int = 0  # percent
    charging: bool = False
    gyro: tuple[int, int, int] = (0, 0, 0)
    accel: tuple[int, int, int] = (0, 0, 0)
    touch: tuple[tuple[bool, int, int], tuple[bool, int, int]] = ((False, 0, 0), (False, 0, 0))
    valid: bool = False

    def pressed(self, name: str) -> bool:
        name = name.lower()
        if name in ("up", "right", "down", "left"):
            return _dpad_has(self.dpad, name)
        if name == "l2_analog":
            return self.l2 > 30
        if name == "r2_analog":
            return self.r2 > 30
        return self.buttons.get(name, False)


def _dpad_has(dpad: int, direction: str) -> bool:
    table = {
        "up": (0, 1, 7),
        "right": (1, 2, 3),
        "down": (3, 4, 5),
        "left": (5, 6, 7),
    }
    return dpad in table[direction]


def parse_input(report: bytes | list[int]) -> InputState | None:
    """Parse a raw input report (including report id) from USB (0x01) or BT (0x31)."""
    data = bytes(report)
    if not data:
        return None
    if data[0] == USB_INPUT_REPORT_ID and len(data) >= 64:
        c = data[1:]
    elif data[0] == BT_INPUT_REPORT_ID and len(data) >= 78:
        c = data[2:]
    else:
        return None  # simple BT report or unknown; full mode not enabled yet
    s = InputState(lx=c[0], ly=c[1], rx=c[2], ry=c[3], l2=c[4], r2=c[5])
    b0, b1, b2 = c[7], c[8], c[9]
    s.dpad = b0 & 0x0F
    btn = s.buttons
    btn["square"] = bool(b0 & 0x10)
    btn["cross"] = bool(b0 & 0x20)
    btn["circle"] = bool(b0 & 0x40)
    btn["triangle"] = bool(b0 & 0x80)
    btn["l1"] = bool(b1 & 0x01)
    btn["r1"] = bool(b1 & 0x02)
    btn["l2"] = bool(b1 & 0x04)
    btn["r2"] = bool(b1 & 0x08)
    btn["create"] = bool(b1 & 0x10)
    btn["options"] = bool(b1 & 0x20)
    btn["l3"] = bool(b1 & 0x40)
    btn["r3"] = bool(b1 & 0x80)
    btn["ps"] = bool(b2 & 0x01)
    btn["touchpad"] = bool(b2 & 0x02)
    btn["mute"] = bool(b2 & 0x04)
    s.gyro = tuple(int.from_bytes(c[15 + 2 * i:17 + 2 * i], "little", signed=True) for i in range(3))
    s.accel = tuple(int.from_bytes(c[21 + 2 * i:23 + 2 * i], "little", signed=True) for i in range(3))
    touches = []
    for i in range(2):
        p = c[32 + 4 * i:36 + 4 * i]
        active = not (p[0] & 0x80)
        x = p[1] | ((p[2] & 0x0F) << 8)
        y = (p[2] >> 4) | (p[3] << 4)
        touches.append((active, x, y))
    s.touch = (touches[0], touches[1])
    status = c[52]
    s.battery = min((status & 0x0F) * 10 + 5, 100)
    charge = (status >> 4) & 0x0F
    s.charging = charge in (0x1, 0x2)
    if charge == 0x2:
        s.battery = 100
    s.valid = True
    return s
