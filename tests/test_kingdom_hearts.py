import struct
import time

from ds5forge import gamedata
from ds5forge.haptics.synth import HapticMixer
from ds5forge.integrations.kingdom_hearts import KHFeel, KingdomHeartsIntegration
from ds5forge.memory import BytesMemory, Field


class Ctx:
    def __init__(self):
        self.mixer = HapticMixer()


def oneshots(ctx):
    return len(ctx.mixer._oneshots) + len(ctx.mixer._scheduled)


def test_damage_heal_lowhp_death_sequence():
    ctx, feel = Ctx(), KHFeel("kh.", {})
    feel.update(ctx, {"hp": 100, "max_hp": 100}, 0.016)
    assert oneshots(ctx) == 0  # first read: no event

    out = feel.update(ctx, {"hp": 70, "max_hp": 100}, 0.016)
    assert oneshots(ctx) >= 2
    assert out.lightbar == (255, 0, 0)
    assert out.left_trigger.type == "vibration" and out.right_trigger.type == "vibration"

    ctx.mixer.silence()
    feel._flash_until = feel._jolt_until = 0
    out = feel.update(ctx, {"hp": 90, "max_hp": 100}, 0.016)
    assert oneshots(ctx) >= 2 and out.lightbar == (0, 255, 90)

    feel._flash_until = feel._jolt_until = 0
    out = feel.update(ctx, {"hp": 15, "max_hp": 100}, 0.016)
    feel._flash_until = feel._jolt_until = 0
    out = feel.update(ctx, {"hp": 15, "max_hp": 100}, 0.016)
    assert out.left_trigger.type == "galloping"  # heartbeat in the triggers
    assert out.lightbar[1] == 0 and out.lightbar[0] > 0
    assert out.player_leds == 0b00001

    ctx.mixer.silence()
    out = feel.update(ctx, {"hp": 0, "max_hp": 100}, 0.016)
    assert oneshots(ctx) >= 2
    out = feel.update(ctx, {"hp": 0, "max_hp": 100}, 0.016)
    assert out.left_trigger.type == "off"  # limp while dead


def test_full_refill_after_load_is_not_a_heal_and_garbage_is_ignored():
    ctx, feel = Ctx(), KHFeel("kh.", {})
    feel.update(ctx, {"hp": 1, "max_hp": 100}, 0.016)
    ctx.mixer.silence()
    feel.update(ctx, {"hp": 100, "max_hp": 100}, 0.016)
    assert oneshots(ctx) == 0
    out = feel.update(ctx, {"hp": 4_000_000_000}, 0.016)
    assert out.lightbar is None


def test_kh1_cutscene_mutes_audio_and_suppresses_events():
    ctx, feel = Ctx(), KHFeel("kh.", {})
    feel.update(ctx, {"hp": 20, "max_hp": 20, "hud": 1.0}, 0.016)
    out = feel.update(ctx, {"hp": 10, "max_hp": 20, "hud": 0.0}, 0.016)
    assert out.mute_audio and oneshots(ctx) == 0


def test_kh2_spell_and_battle_alerts():
    ctx, feel = Ctx(), KHFeel("kh.", {})
    feel.update(ctx, {"hp": 60, "max_hp": 60, "mp": 100, "max_mp": 100, "battle": 0}, 0.016)
    feel.update(ctx, {"hp": 60, "max_hp": 60, "mp": 88, "max_mp": 100, "battle": 2}, 0.016)
    assert oneshots(ctx) >= 3  # cast chirp + double alert pulse


def test_integration_reads_fake_game_memory():
    mem = BytesMemory(bytearray(0x1000))
    mem.data[0x10] = 106
    game = gamedata.GameData("test", "Test KH", ["test.exe"], {
        "v1": gamedata.Version("v1", {"offset": "0x10", "type": "u8", "equals": 106},
                               {"hp": Field("hp", 0x100, "i32"), "max_hp": Field("max_hp", 0x104, "i32")}),
    })
    struct.pack_into("<ii", mem.data, 0x100, 50, 50)
    integ = KingdomHeartsIntegration({"games": []})
    integ.attach(mem, game)
    ctx = Ctx()
    integ.tick(ctx, 0.016)
    assert "HP 50/50" in integ.status()
    struct.pack_into("<i", mem.data, 0x100, 30)
    time.sleep(0.02)
    out = integ.tick(ctx, 0.016)
    assert out.lightbar == (255, 0, 0)


def test_unknown_hp_status_points_to_wizard():
    mem = BytesMemory(bytearray(0x1000))
    integ = KingdomHeartsIntegration({"games": []})
    bbs = gamedata.load("bbs")
    mem2 = BytesMemory(bytearray(0x800000))
    mem2.data[0x726464] = 106
    integ.attach(mem2, bbs)
    assert "Find HP" in integ.status()
    integ.attach(mem, bbs)
    assert "unknown game version" in integ.status()
