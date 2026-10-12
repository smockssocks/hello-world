import time

from ds5forge import protocol as p
from ds5forge.controller import Controller
from ds5forge.engine import Engine
from ds5forge.haptics.outputs import BluetoothHapticsOutput
from ds5forge.haptics.synth import HapticMixer
from ds5forge.profiles import Profile, load_profiles
from ds5forge.settings import Settings


class FakeDevice:
    def __init__(self, bt=False):
        self.bt = bt
        self.writes = []
        self.closed = False

    def open_path(self, path):
        pass

    def read(self, size, timeout=0):
        time.sleep(0.002)
        c = bytearray(63)
        c[0:6] = bytes([128, 128, 128, 128, 0, 200])
        c[7] = 0x08
        c[52] = 0x08
        if self.bt:
            return list(bytes([0x31, 0]) + bytes(c) + bytes(13))
        return list(bytes([0x01]) + bytes(c))

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def get_feature_report(self, rid, size):
        return [rid] + [0] * (size - 1)

    def close(self):
        self.closed = True


class FakeHid:
    def __init__(self, bt=False):
        self.dev = FakeDevice(bt)

    def enumerate(self, vid, pid):
        if pid != p.DUALSENSE_PID:
            return []
        return [{"path": b"fake", "product_string": "DualSense Wireless Controller",
                 "interface_number": -1 if self.dev.bt else 3, "usage_page": 1, "usage": 5}]

    def device(self):
        return self.dev


class NoCapture:
    running = False
    error = "disabled in tests"
    device_name = ""
    transient = 0.0

    def start(self):
        return False

    def stop(self):
        pass


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def make_engine(bt=False, **settings):
    hid = FakeHid(bt)
    s = Settings(auto_profiles=False, audio_capture=False, **settings)
    eng = Engine(settings=s, controller=Controller(hid_module=hid), capture=NoCapture())
    return eng, hid.dev


def test_engine_usb_falls_back_to_rumble_and_applies_profile_triggers():
    eng, dev = make_engine()
    prof = Profile.from_dict({"name": "T", "triggers": {"right": {"type": "weapon", "start": 3, "end": 5, "strength": 8}}})
    eng.profiles["T"] = prof
    eng.settings.profile = "T"
    eng.start()
    try:
        assert wait_for(lambda: eng.controller.connected)
        assert wait_for(lambda: any(w[0] == 0x02 and w[1 + p.OFF_RIGHT_TRIGGER] == 0x25 for w in dev.writes))
        assert wait_for(lambda: eng.output is not None)
        assert eng.output.name == "rumble"  # no audio device in tests
        assert eng.controller.input.r2 == 200
        st = eng.status()
        assert st["connected"] and st["transport"] == "USB" and st["profile"] == "T"
    finally:
        eng.stop()
    # On exit triggers are released.
    last_trig = [w for w in dev.writes if w[1] & p.FLAG0_RIGHT_TRIGGER][-1]
    assert last_trig[1 + p.OFF_RIGHT_TRIGGER] == 0x05


def test_engine_bluetooth_streams_haptics_reports():
    eng, dev = make_engine(bt=True)
    eng.start()
    try:
        assert wait_for(lambda: isinstance(eng.output, BluetoothHapticsOutput))
        assert wait_for(lambda: sum(1 for w in dev.writes if w[0] == 0x32) > 10)
        assert any(w[0] == 0x31 for w in dev.writes)
        assert eng.controller.transport == "bt"
    finally:
        eng.stop()


def test_lab_override_beats_profile():
    eng, dev = make_engine()
    eng.lab_override = (None, __import__("ds5forge.triggers", fromlist=["x"]).PRESETS["Bow"])
    eng.start()
    try:
        assert wait_for(lambda: any(w[1] & p.FLAG0_RIGHT_TRIGGER and w[1 + p.OFF_RIGHT_TRIGGER] == 0x22
                                    for w in dev.writes if w[0] == 0x02))
    finally:
        eng.stop()


def test_bt_haptics_rate():
    writes = []

    class C:
        def write_raw(self, data):
            writes.append(data)
            return True

    out = BluetoothHapticsOutput(HapticMixer(), C())
    out.start()
    time.sleep(0.5)
    out.stop()
    # ~94 reports per second
    assert 30 <= len(writes) <= 60


def test_builtin_profiles_load():
    profiles = load_profiles()
    assert "Default (any game)" in profiles
    assert profiles["Cyberpunk 2077"].hands_off and profiles["Cyberpunk 2077"].haptics == "bluetooth_only"
    assert profiles["Skyrim Special Edition / Anniversary"].xbox_bridge
    assert "SkyrimSE.exe" in profiles["Skyrim Special Edition / Anniversary"].exe
    shooter = profiles["Shooter (any game)"]
    inp = p.InputState()
    inp.buttons["l2"] = True
    _, right = shooter.triggers_for(inp)
    assert right.type == "weapon"


def test_connect_does_not_force_rumble_mode():
    hid = FakeHid()
    c = Controller(hid_module=hid)
    c.start()
    try:
        assert wait_for(lambda: len(hid.dev.writes) > 0)
        first = hid.dev.writes[0]
        assert first[1] & (p.FLAG0_COMPATIBLE_VIBRATION | p.FLAG0_HAPTICS_SELECT) == 0
        assert first[1 + p.OFF_FLAG2] & p.FLAG2_LIGHTBAR_SETUP
    finally:
        c.stop()


def test_hands_off_profile_only_forwards_mod_effects():
    import json
    import socket

    eng, dev = make_engine()
    prof = Profile.from_dict({"name": "Native", "hands_off": True, "haptics": "bluetooth_only"})
    eng.profiles["Native"] = prof
    eng.settings.profile = "Native"
    eng.start()
    try:
        assert wait_for(lambda: eng.controller.connected)
        assert wait_for(lambda: eng._output_key is not None)
        assert eng.output is None  # wired + bluetooth_only -> game owns the haptics
        time.sleep(0.2)
        n = len(dev.writes)
        time.sleep(0.2)
        assert len(dev.writes) == n  # nothing written while hands-off
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        c.sendto(json.dumps({"instructions": [{"type": 1, "parameters": [0, 2, 16, 2, 7, 8]}]}).encode(),
                 ("127.0.0.1", eng.dsx.port))
        assert wait_for(lambda: any(w[1] & p.FLAG0_RIGHT_TRIGGER and w[1 + p.OFF_RIGHT_TRIGGER] == 0x25
                                    for w in dev.writes))
    finally:
        eng.stop()
