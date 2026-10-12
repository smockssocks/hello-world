import json
import socket
import time

from ds5forge import dsx_server as d
from ds5forge import protocol as p
from ds5forge import triggers as t


def packet(*instructions):
    return json.dumps({"instructions": [{"type": k, "parameters": list(v)} for k, v in instructions]}).encode()


def test_resistance_and_bow_match_native_builders():
    srv = d.DsxServer()
    # Same shapes as the official DSX v2 UDP example.
    srv.handle(packet((1, (0, 1, 13, 0, 8)), (1, (0, 2, 14, 0, 8, 2, 5))))
    st = srv.snapshot()
    assert st.left == t.feedback(0, 8)
    assert st.right == t.bow(0, 8, 2, 5)


def test_enum_names_and_numeric_strings_are_accepted():
    srv = d.DsxServer()
    srv.handle(packet((1, (0, "Right", "SemiAutomaticGun", "2", "7", "8"))))
    assert srv.snapshot().right == t.weapon(2, 7, 8)


def test_fixed_and_custom_modes():
    srv = d.DsxServer()
    srv.handle(packet((1, (0, 1, 7)), (1, (0, 2, 12, 4, 10, 255, 0, 0, 0, 0, 0))))
    st = srv.snapshot()
    assert st.left[:3] == bytes([0x01, 0, 255])
    assert st.right[:3] == bytes([0x25, 10, 255])
    srv.handle(packet((1, (0, 1, 0))))
    assert srv.snapshot().left == p.TRIGGER_OFF  # Normal releases the trigger


def test_sony_style_modes():
    srv = d.DsxServer()
    srv.handle(packet((1, (0, 1, 23, 3, 6, 40)), (1, (0, 2, 24, 1, 8, 1, 8))))
    st = srv.snapshot()
    assert st.left == t.vibration(3, 6, 40)
    assert st.right == t.slope_feedback(1, 8, 1, 8)


def test_lights_and_reset():
    srv = d.DsxServer()
    srv.handle(packet((2, (0, 255, 10, 0)), (6, (0, 2)), (5, (0, 1))))
    st = srv.snapshot()
    assert st.lightbar == (255, 10, 0) and st.player_leds == 0x15 and st.mic_led == 2
    srv.handle(packet((3, (0, True, False, True, False, True))))
    assert srv.snapshot().player_leds == 0b10101
    srv.handle(packet((7, (0,))))
    st = srv.snapshot()
    assert st.lightbar is None and st.left is None


def test_garbage_is_rejected_without_state_change():
    srv = d.DsxServer()
    try:
        srv.handle(b"not json")
    except ValueError:
        pass
    srv.handle(packet((1, (0, 1, 999))))
    assert srv.snapshot().left is None


def test_udp_roundtrip_reply():
    srv = d.DsxServer(port=0, status_fn=lambda: (True, 80, "bt"))
    # bind to an ephemeral port for the test
    srv.port = _free_port()
    assert srv.start()
    try:
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        c.settimeout(2)
        c.sendto(packet((1, (0, 2, 13, 2, 6))), ("127.0.0.1", srv.port))
        reply = json.loads(c.recv(4096))
        assert reply["Status"] == "DSX Received UDP Instructions"
        assert reply["isControllerConnected"] and reply["BatteryLevel"] == 80
        assert reply["Devices"][0]["ConnectionType"] == 1
        assert srv.snapshot().right == t.feedback(2, 6)
        assert srv.receiving
    finally:
        srv.stop()


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_text_file_format():
    left, right = d.parse_text_file(
        "LeftTrigger=Resistance\nForceLeftTrigger=(2)(6)\n"
        "RightTrigger=CustomTriggerValue\nCustomTriggerValueRightMode=Rigid A\n"
        "ForceRightTrigger=(10)(255)(0)(0)(0)(0)(0)\n")
    assert left == t.feedback(2, 6)
    assert right[:3] == bytes([0x21, 10, 255])
    left, right = d.parse_text_file("LeftTrigger=Normal\nRightTrigger=VibrateTrigger\nVibrateTriggerIntensity=30\n")
    assert left == p.TRIGGER_OFF and right[:3] == bytes([0x06, 30, 255])


def test_find_and_watch_mod_text_file(tmp_path):
    mods = tmp_path / "plugins" / "cyber_engine_tweaks" / "mods" / "dualsense"
    mods.mkdir(parents=True)
    (mods / "readme.txt").write_text("nothing here")
    f = mods / "triggers.txt"
    f.write_text("LeftTrigger=Rigid\n")
    found = d.find_mod_text_file(tmp_path / "plugins")
    assert found == f
    w = d.TextFileWatcher(found)
    left, right = w.poll()
    assert left[:3] == bytes([0x01, 0, 255]) and right is None
    time.sleep(0.04)
    assert w.poll() is None  # unchanged file
