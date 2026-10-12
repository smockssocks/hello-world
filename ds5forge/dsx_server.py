"""DSX-compatible mod server.

Lots of free game mods (Skyrim, Cyberpunk 2077, Fallout 4, RDR2, ...) were made for
the paid DSX app: they send JSON instructions over UDP to 127.0.0.1:6969 whenever
something happens in game (weapon drawn, bow pulled, car driven...). This module
speaks the same protocol (DSX v2, as used by DSX mods and DualSenseY), so those
mods drive the DualSense through DS5 Forge instead.

Packet: {"instructions": [{"type": 1, "parameters": [controller, trigger, mode, ...]}]}
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import protocol as p
from . import triggers as t

log = logging.getLogger(__name__)

DEFAULT_PORT = 6969
PORT_FILE = Path(r"C:\Temp\DualSenseX\DualSenseX_PortNumber.txt")

# InstructionType
GET_STATUS, TRIGGER_UPDATE, RGB_UPDATE, PLAYER_LED, TRIGGER_THRESHOLD, MIC_LED, \
    PLAYER_LED_NEW, RESET_TO_USER = range(8)

TRIGGER_NAMES = {"invalid": 0, "left": 1, "right": 2}

MODE_NAMES = [
    "Normal", "GameCube", "VerySoft", "Soft", "Hard", "VeryHard", "Hardest", "Rigid",
    "VibrateTrigger", "Choppy", "Medium", "VibrateTriggerPulse", "CustomTriggerValue",
    "Resistance", "Bow", "Galloping", "SemiAutomaticGun", "AutomaticGun", "Machine",
    "VIBRATE_TRIGGER_10Hz", "OFF", "FEEDBACK", "WEAPON", "VIBRATION", "SLOPE_FEEDBACK",
    "MULTIPLE_POSITION_FEEDBACK", "MULTIPLE_POSITION_VIBRATION",
]
MODES = {name.lower(): i for i, name in enumerate(MODE_NAMES)}

CUSTOM_MODE_NAMES = [
    "OFF", "Rigid", "RigidA", "RigidB", "RigidAB", "Pulse", "PulseA", "PulseB", "PulseAB",
    "VibrateResistance", "VibrateResistanceA", "VibrateResistanceB", "VibrateResistanceAB",
    "VibratePulse", "VibratePulseA", "VibratePulsB", "VibratePulseAB",
]
CUSTOM_MODES = {name.lower(): i for i, name in enumerate(CUSTOM_MODE_NAMES)}
CUSTOM_MODES["vibratepulseb"] = 15

MIC_NAMES = {"on": 0, "pulse": 1, "off": 2}
PLAYER_LED_NEW_NAMES = {"one": 0, "two": 1, "three": 2, "four": 3, "five": 4, "alloff": 5}
PLAYER_LED_PATTERNS = [0x04, 0x0A, 0x15, 0x1B, 0x1F, 0x00]

# Raw "legacy" trigger mode bytes understood by the DualSense firmware.
RAW_OFF, RAW_RIGID, RAW_PULSE = 0x00, 0x01, 0x02
RAW_RIGID_A, RAW_RIGID_B, RAW_RIGID_AB = 0x21, 0x05, 0x25
RAW_PULSE_A, RAW_PULSE_A2, RAW_PULSE_B, RAW_PULSE_B2, RAW_PULSE_AB = 0x22, 0x23, 0x06, 0x26, 0x27

CUSTOM_TO_RAW = [RAW_OFF, RAW_RIGID, RAW_RIGID_A, RAW_RIGID_B, RAW_RIGID_AB, RAW_PULSE, RAW_PULSE_A,
                 RAW_PULSE_B, RAW_PULSE_AB, RAW_PULSE_B, RAW_PULSE_B2, RAW_PULSE_B, RAW_PULSE_B2,
                 RAW_PULSE_AB, RAW_PULSE_AB, RAW_PULSE_AB, RAW_PULSE_AB]

FIXED_MODES = {
    "normal": [RAW_RIGID_B],
    "gamecube": [RAW_PULSE, 144, 160, 255],
    "verysoft": [RAW_PULSE, 128, 160, 255],
    "soft": [RAW_RIGID_A, 69, 160, 255],
    "hard": [RAW_RIGID_A, 32, 160, 255, 255, 255, 255, 255],
    "veryhard": [RAW_RIGID_A, 16, 160, 255, 255, 255, 255, 255],
    "hardest": [RAW_PULSE, 0, 255, 255, 255, 255, 255, 255],
    "rigid": [RAW_RIGID, 0, 255],
    "choppy": [RAW_RIGID_A, 2, 39, 33, 39, 38, 2],
    "medium": [RAW_PULSE_A, 2, 35, 1, 6, 6, 1, 33],
    "vibratetriggerpulse": [RAW_PULSE_AB, 37, 35, 6, 39, 33, 35, 34],
    "vibrate_trigger_10hz": [RAW_PULSE_B, 10, 255, 40],
}


def _pad(values) -> bytes:
    out = bytearray(11)
    for i, v in enumerate(list(values)[:11]):
        out[i] = int(v) & 0xFF
    return bytes(out)


def _num(v, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _enum(v, names: dict[str, int]) -> int:
    if isinstance(v, str) and not v.strip().lstrip("-").isdigit():
        return names.get(v.strip().replace(" ", "").lower(), -1)
    return _num(v, -1)


def trigger_bytes(mode, args: list) -> bytes | None:
    """Translate a DSX TriggerUpdate (mode + its parameters) to an 11-byte effect."""
    m = _enum(mode, MODES)
    if not 0 <= m < len(MODE_NAMES):
        return None
    name = MODE_NAMES[m].lower()
    a = [_num(x) for x in args]

    def arg(i, default=0):
        return a[i] if i < len(a) else default

    if name in FIXED_MODES:
        return _pad(FIXED_MODES[name])
    if name == "vibratetrigger":
        freq = arg(0, 40) or 40
        return _pad([RAW_PULSE_B, freq, 255, 0])
    if name == "customtriggervalue":
        cm = _enum(args[0], CUSTOM_MODES) if args else 0
        raw = CUSTOM_TO_RAW[cm] if 0 <= cm < len(CUSTOM_TO_RAW) else RAW_OFF
        vals = a[1:8] + [0] * (7 - len(a[1:8]))
        return _pad([raw] + vals + [0, 0, arg(8)])
    if name in ("resistance", "feedback"):
        return t.feedback(arg(0), arg(1))
    if name == "bow":
        return t.bow(arg(0), arg(1), arg(2), arg(3))
    if name == "galloping":
        return t.galloping(arg(0), arg(1), arg(2), arg(3), arg(4))
    if name in ("semiautomaticgun", "weapon"):
        return t.weapon(arg(0), arg(1), arg(2))
    if name == "automaticgun":
        return t.vibration(arg(0), arg(1), arg(2))
    if name == "machine":
        return t.machine(arg(0), arg(1), arg(2), arg(3), arg(4), arg(5))
    if name == "off":
        return p.TRIGGER_OFF
    if name == "vibration":
        return t.vibration(arg(0), arg(1), arg(2))
    if name == "slope_feedback":
        return t.slope_feedback(arg(0), arg(1), arg(2), arg(3))
    if name == "multiple_position_feedback":
        return t.multi_feedback(a[:10] + [0] * (10 - len(a[:10])))
    if name == "multiple_position_vibration":
        amps = a[1:11]
        return t.multi_vibration(arg(0), amps + [0] * (10 - len(amps)))
    return None


class DsxState:
    """What the mods asked for. ``None`` means "no request, let the profile decide"."""

    def __init__(self):
        self.left: bytes | None = None
        self.right: bytes | None = None
        self.lightbar: tuple[int, int, int] | None = None
        self.player_leds: int | None = None
        self.mic_led: int | None = None
        self.thresholds = [0, 0]
        self.last_update = 0.0

    def reset(self) -> None:
        self.__init__()


class DsxServer:
    """UDP server compatible with DSX v2 mods."""

    def __init__(self, port: int = DEFAULT_PORT, status_fn=None):
        self.port = port
        self.state = DsxState()
        self.status_fn = status_fn  # () -> (connected, battery, transport)
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.error: str | None = None
        self.packets = 0

    @property
    def running(self) -> bool:
        return self._sock is not None

    @property
    def receiving(self) -> bool:
        return time.monotonic() - self.state.last_update < 5.0

    def start(self) -> bool:
        if self._sock is not None:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", self.port))
            sock.settimeout(0.5)
        except OSError as exc:
            self.error = ("Port %d is in use - close DSX / DualSenseY if they're running (%s)" % (self.port, exc))
            log.warning(self.error)
            return False
        self._sock = sock
        self._running = True
        self.error = None
        self._write_port_file()
        self._thread = threading.Thread(target=self._run, name="dsx-server", daemon=True)
        self._thread.start()
        log.info("Mod server (DSX compatible) listening on 127.0.0.1:%d", self.port)
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.5)
        if self._sock:
            self._sock.close()
            self._sock = None

    def _write_port_file(self) -> None:
        """DSX mods look up the port in this file."""
        if sys.platform != "win32":
            return
        try:
            PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
            PORT_FILE.write_text(str(self.port), encoding="ascii")
        except OSError as exc:
            log.debug("could not write %s: %s", PORT_FILE, exc)

    def snapshot(self) -> DsxState:
        with self._lock:
            s = DsxState()
            s.__dict__.update(self.state.__dict__)
            return s

    def reset(self) -> None:
        with self._lock:
            self.state.reset()

    def apply_text(self, left: bytes | None, right: bytes | None) -> None:
        """Feed trigger effects that came from a text-file mod."""
        with self._lock:
            if left is not None:
                self.state.left = left
            if right is not None:
                self.state.right = right
            self.state.last_update = time.monotonic()
            self.packets += 1

    def _run(self) -> None:
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.handle(data)
            except Exception as exc:
                log.debug("bad DSX packet: %s", exc)
                continue
            try:
                self._sock.sendto(self.response().encode("utf-8"), addr)
            except OSError:
                pass

    def handle(self, data: bytes) -> None:
        text = data.decode("utf-8", errors="replace").strip("\x00 \r\n\t")
        packet = json.loads(text)
        instructions = packet.get("instructions") or packet.get("Instructions") or []
        with self._lock:
            st = self.state
            for ins in instructions:
                if not isinstance(ins, dict):
                    continue
                kind = _num(ins.get("type", ins.get("Type")), -1)
                params = ins.get("parameters", ins.get("Parameters")) or []
                self._apply(st, kind, params)
            st.last_update = time.monotonic()
            self.packets += 1

    @staticmethod
    def _apply(st: DsxState, kind: int, params: list) -> None:
        if kind == TRIGGER_UPDATE and len(params) >= 3:
            side = _enum(params[1], TRIGGER_NAMES)
            effect = trigger_bytes(params[2], list(params[3:]))
            if effect is None:
                return
            if side == 1:
                st.left = effect
            elif side == 2:
                st.right = effect
        elif kind == RGB_UPDATE and len(params) >= 4:
            st.lightbar = (_num(params[1]) & 0xFF, _num(params[2]) & 0xFF, _num(params[3]) & 0xFF)
        elif kind == PLAYER_LED and len(params) >= 6:
            mask = 0
            for i, on in enumerate(params[1:6]):
                if on is True or str(on).lower() in ("true", "1"):
                    mask |= 1 << i
            st.player_leds = mask
        elif kind == PLAYER_LED_NEW and len(params) >= 2:
            idx = _enum(params[1], PLAYER_LED_NEW_NAMES)
            if 0 <= idx < len(PLAYER_LED_PATTERNS):
                st.player_leds = PLAYER_LED_PATTERNS[idx]
        elif kind == MIC_LED and len(params) >= 2:
            mode = _enum(params[1], MIC_NAMES)
            st.mic_led = {0: 1, 1: 2, 2: 0}.get(mode)
        elif kind == TRIGGER_THRESHOLD and len(params) >= 3:
            side = _enum(params[1], TRIGGER_NAMES)
            if side in (1, 2):
                st.thresholds[side - 1] = _num(params[2]) & 0xFF
        elif kind == RESET_TO_USER:
            st.reset()

    def response(self) -> str:
        connected, battery, transport = self.status_fn() if self.status_fn else (False, 0, None)
        devices = []
        if connected:
            devices.append({
                "Index": 1, "MacAddress": "", "DeviceType": 0,
                "ConnectionType": 1 if transport == "bt" else 0, "BatteryLevel": battery,
                "IsSupportAT": True, "IsSupportLightBar": True, "IsSupportPlayerLED": True, "IsSupportMicLED": True,
            })
        return json.dumps({
            "Status": "DSX Received UDP Instructions",
            "TimeReceived": datetime.now().strftime("%Y/%m/%d %I:%M:%S %p"),
            "isControllerConnected": connected,
            "BatteryLevel": battery,
            "Devices": devices,
        })


# ------------------------------------------------------------ text-file mods
# Older DSX mods (e.g. the Cyberpunk 2077 "DualSense Adaptive Trigger Effects" CET mod)
# write a small text file instead of using UDP:
#   LeftTrigger=Resistance
#   ForceLeftTrigger=(2)(6)
#   RightTrigger=CustomTriggerValue
#   CustomTriggerValueRightMode=Rigid A
#   ForceRightTrigger=(10)(255)(0)(0)(0)(0)(0)

TEXT_KEYS = ("LeftTrigger=", "RightTrigger=")


def parse_text_file(text: str) -> tuple[bytes | None, bytes | None]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            values[key.strip().lower()] = val.strip()

    def side(name: str) -> bytes | None:
        mode = values.get(name + "trigger")
        if not mode:
            return None
        forces = [x for x in values.get("force%strigger" % name, "").replace(")", "(").split("(") if x.strip()]
        key = mode.replace(" ", "").lower()
        if key == "customtriggervalue":
            args = [values.get("customtriggervalue%smode" % name, "OFF")] + forces
        elif key == "vibratetrigger":
            args = [values.get("vibratetriggerintensity", "40")]
        else:
            args = forces
        return trigger_bytes(mode.replace(" ", ""), args)

    return side("left"), side("right")


def find_mod_text_file(root: Path, max_files: int = 4000) -> Path | None:
    """Look under ``root`` for a small .txt file that a DSX text-file mod writes to."""
    if not root.is_dir():
        return None
    seen = 0
    for path in root.rglob("*.txt"):
        seen += 1
        if seen > max_files:
            break
        try:
            if path.stat().st_size > 4096:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(k in text for k in TEXT_KEYS):
            return path
    return None


class TextFileWatcher:
    """Polls a DSX text file and returns new trigger effects when it changes."""

    def __init__(self, path: Path):
        self.path = path
        self._mtime = None
        self._last_check = 0.0

    def poll(self) -> tuple[bytes | None, bytes | None] | None:
        now = time.monotonic()
        if now - self._last_check < 0.03:
            return None
        self._last_check = now
        try:
            mtime = self.path.stat().st_mtime_ns
            if mtime == self._mtime:
                return None
            text = self.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        self._mtime = mtime
        return parse_text_file(text)
