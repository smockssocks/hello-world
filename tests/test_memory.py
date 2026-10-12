import json
import struct

from ds5forge import gamedata
from ds5forge.memory import BytesMemory, Field
from ds5forge.scanner import Scanner


def make_mem(size=0x10000):
    data = bytearray(size)
    for i in range(0, size, 4):  # lots of noise values that look like HP
        data[i] = (i * 7) % 200
    return BytesMemory(data)


def test_field_reads_and_pointer_chain():
    mem = make_mem()
    struct.pack_into("<i", mem.data, 0x100, 1234)
    struct.pack_into("<Q", mem.data, 0x200, mem.base + 0x300)
    struct.pack_into("<H", mem.data, 0x308, 77)
    assert Field("hp", 0x100, "i32").read(mem) == 1234
    assert Field("x", 0x200, "u16", [8]).read(mem) == 77


def test_scanner_finds_hp_like_a_player_would():
    mem = make_mem()
    hp = 0x4444
    struct.pack_into("<I", mem.data, hp, 120)
    sc = Scanner(mem, size=4)
    assert sc.start() > 1000
    # take a hit
    struct.pack_into("<I", mem.data, hp, 95)
    for i in range(0, 0x8000, 64):  # unrelated memory changes too
        mem.data[i] = (mem.data[i] + 1) % 256
    sc.step("decreased")
    sc.step("unchanged")
    struct.pack_into("<I", mem.data, hp, 60)
    sc.step("decreased")
    struct.pack_into("<I", mem.data, hp, 120)
    sc.step("increased")
    assert sc.candidates() == [(hp, 120)]
    assert sc.read_offset(hp) == 120


def test_scanner_word_size_and_equals():
    mem = make_mem()
    struct.pack_into("<H", mem.data, 0x2002, 300)
    sc = Scanner(mem, size=2)
    sc.start()
    sc.step("equals", 300)
    assert (0x2002, 300) in sc.candidates()


def test_version_detection_and_user_override(tmp_path):
    mem = make_mem(0x800000)
    mem.data[0x660EF4] = 106  # KH2 Steam Global version marker
    kh2 = gamedata.load("kh2")
    v = kh2.detect(mem)
    assert v is not None and v.name == "Steam Global 1.0.0.2"
    assert v.fields["hp"].offset == 0x2A23598 and v.fields["max_hp"].offset == 0x2A2359C

    bbs = gamedata.load("bbs")
    assert "hp" not in bbs.versions["Steam Global 1.0.0.2"].fields
    # unknown build -> saved under an image-size check
    vname = gamedata.save_user_field(bbs, mem, None, "hp", 0x1234, "u16")
    bbs = gamedata.load("bbs")
    assert bbs.detect(mem).name == vname
    assert bbs.detect(mem).fields["hp"].offset == 0x1234
    # known build -> merged into that build's fields
    mem.data[0x726464] = 106
    bbs = gamedata.load("bbs")
    gamedata.save_user_field(bbs, mem, bbs.versions["Steam Global 1.0.0.2"], "hp", 0x5555, "u16")
    bbs = gamedata.load("bbs")
    fields = bbs.versions["Steam Global 1.0.0.2"].fields
    assert fields["hp"].offset == 0x5555 and "location" in fields
    saved = json.loads((gamedata.user_dir() / "bbs.json").read_text())
    assert "Steam Global 1.0.0.2" in saved["versions"]


import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process memory API")
def test_windows_process_memory_reads_own_image():
    from ds5forge.memory import ProcessMemory
    mem = ProcessMemory(os.getpid(), "python.exe")
    try:
        assert mem.alive()
        assert mem.read(mem.base, 2) == b"MZ"  # PE header of the main module
        assert mem.read_value(0, "u16") == 0x5A4D
        regions = mem.regions(module_only=True)
        assert regions and regions[0][0] >= mem.base
        sc = Scanner(mem, size=2, min_value=0x5A4D, max_value=0x5A4D)
        assert sc.start() >= 1 and (0, 0x5A4D) in sc.candidates(200)
    finally:
        mem.close()
