"""Kingdom Hearts (1.5+2.5 collection, Steam/Epic PC) game-state feedback.

Reads HP/MP/battle state straight from the game's memory (read-only, same
addresses the KH randomizer and speedrun Lua tools use) and turns it into:

* damage    -> impact haptic scaled by how much HP you lost, red lightbar flash, trigger jolt
* healing   -> soft rising "cure" shimmer, green flash
* low HP    -> heartbeat in the haptics *and* the triggers, lightbar pulses red
* death     -> long falling rumble, triggers go limp, lightbar fades
* MP (KH2)  -> spell-cast chirp, chime when MP recharges
* battles (KH2) -> alert pulse when enemies or a boss appear
* lightbar  -> HP colour (green -> yellow -> red); player LEDs as an HP bar
* cutscenes (KH1) -> sound-based haptics pause so cutscene audio doesn't buzz
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from .. import gamedata
from ..haptics import synth
from ..memory import Memory, MemoryError_, open_process
from ..triggers import TriggerEffect
from .base import Integration, Outputs

log = logging.getLogger(__name__)

KH_GAMES = ("bbs", "kh1", "kh2")
ATTACH_INTERVAL = 2.0
READ_INTERVAL = 1 / 60
MAX_SANE_HP = 20000


class KingdomHeartsIntegration(Integration):
    id = "kingdom_hearts"
    name = "Kingdom Hearts game state"
    games = "Birth by Sleep, Kingdom Hearts Final Mix, Kingdom Hearts II Final Mix (Steam)"
    setup = ["Nothing to install. Start the game; DS5 Forge reads HP and other state directly (read-only)."]

    def __init__(self, settings: dict[str, Any] | None = None):
        super().__init__(settings)
        ids = self.settings.get("games") or KH_GAMES
        self.games = [g for g in (gamedata.load(i) for i in ids) if g is not None]
        self.mem: Memory | None = None
        self.game: gamedata.GameData | None = None
        self.version: gamedata.Version | None = None
        self.state: dict[str, Any] = {}
        self.error: str | None = None
        self._last_attach = 0.0
        self._last_read = 0.0
        self.feel = KHFeel(self.id + ".", self.settings)

    # ---------------------------------------------------------------- attach
    def attach(self, mem: Memory, game: gamedata.GameData) -> None:
        self.mem, self.game = mem, game
        self.version = game.detect(mem)
        self.feel.reset()
        log.info("Reading %s (%s)", game.name, self.version.name if self.version else "unknown version")

    def reload(self) -> None:
        """Re-read the game data files (after the wizard saved a new value)."""
        ids = [g.id for g in self.games]
        self.games = [g for g in (gamedata.load(i) for i in ids) if g is not None]
        if self.mem is not None and self.game is not None:
            game = next((g for g in self.games if g.id == self.game.id), self.game)
            self.attach(self.mem, game)

    def detach(self) -> None:
        if self.mem is not None:
            self.mem.close()
        self.mem = self.game = self.version = None
        self.state = {}

    def _try_attach(self) -> None:
        now = time.monotonic()
        if now - self._last_attach < ATTACH_INTERVAL:
            return
        self._last_attach = now
        for game in self.games:
            mem = open_process(game.process)
            if mem is not None:
                self.attach(mem, game)
                return

    def stop(self, ctx) -> None:
        self.detach()
        super().stop(ctx)

    # ------------------------------------------------------------------ read
    def read_state(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.version is None:
            return out
        for name, fld in self.version.fields.items():
            try:
                out[name] = fld.read(self.mem)
            except (MemoryError_, OSError, Exception):
                out[name] = None
        return out

    def tick(self, ctx, dt: float) -> Outputs:
        if self.mem is None:
            self._try_attach()
            if self.mem is None:
                return Outputs()
        if not self.mem.alive():
            log.info("%s closed", self.game.name if self.game else "Game")
            self.detach()
            self.feel.reset()
            ctx.mixer.silence(self.id + ".")
            return Outputs()
        now = time.monotonic()
        if now - self._last_read >= READ_INTERVAL:
            self._last_read = now
            self.state = self.read_state()
            if self.state:
                self.last_data = now
                self.packets += 1
        return self.feel.update(ctx, self.state, dt)

    def status(self) -> str:
        if self.mem is None:
            return "Waiting for a Kingdom Hearts game to start..."
        name = self.game.name if self.game else "?"
        if self.version is None:
            return "%s: unknown game version - use the Game Data tab to find HP" % name
        hp, mx = self.state.get("hp"), self.state.get("max_hp") or self.feel.max_hp_seen
        if "hp" not in self.version.fields:
            return "%s (%s): HP address not known yet - use Game Data > Find HP" % (name, self.version.name)
        return "%s (%s): HP %s/%s" % (name, self.version.name, hp, mx or "?")


class KHFeel:
    """Game-state -> haptics/triggers/lights. Settings (in the profile's integration_settings):

    damage (0-1.5, default 1), heal (0-1), heartbeat (bool), low_hp (fraction, default 0.25),
    trigger_effects (bool), lightbar ("hp" | "off"), player_leds (bool), mp (bool),
    battle_alerts (bool), quiet_cutscenes (bool)
    """

    def __init__(self, prefix: str, settings: dict[str, Any]):
        self.prefix = prefix
        self.settings = settings
        self.reset()

    def reset(self) -> None:
        self.prev_hp: int | None = None
        self.prev_mp: int | None = None
        self.prev_battle: int | None = None
        self.max_hp_seen = 0
        self.max_mp_seen = 0
        self._next_beat = 0.0
        self._flash_until = 0.0
        self._flash_color = (255, 0, 0)
        self._jolt_until = 0.0
        self._dead_until = 0.0
        self._t = 0.0

    def _s(self, key: str, default):
        return self.settings.get(key, default)

    def update(self, ctx, state: dict[str, Any], dt: float) -> Outputs:
        out = Outputs()
        self._t += dt
        hp = state.get("hp")
        if hp is None or not (0 <= hp <= MAX_SANE_HP):
            return out
        now = time.monotonic()
        mixer = ctx.mixer
        hud = state.get("hud")
        in_play = hud is None or hud > 0.0
        if hud is not None and self._s("quiet_cutscenes", True):
            out.mute_audio = not in_play

        max_hp = state.get("max_hp")
        if not max_hp or not (0 < max_hp <= MAX_SANE_HP) or max_hp < hp:
            self.max_hp_seen = max(self.max_hp_seen, hp)
            max_hp = self.max_hp_seen
        frac = hp / max_hp if max_hp else 1.0
        dmg_scale = float(self._s("damage", 1.0))

        if self.prev_hp is not None and in_play:
            delta = hp - self.prev_hp
            if delta < 0:
                lost = -delta / max_hp if max_hp else 0.3
                if hp == 0:
                    self._on_death(mixer, now)
                else:
                    amp = min(1.0, (0.45 + lost * 2.5) * dmg_scale)
                    mixer.play(synth.impact(amp, duration=0.12 + min(lost, 0.5)))
                    mixer.play(synth.thump(amp * 0.8))
                    self._flash((255, 0, 0), now, 0.25)
                    self._jolt_until = now + 0.12
            elif delta > 0:
                if self.prev_hp == 0:
                    mixer.play(synth.Tone(60, 0.6, 0.6, attack=0.3, release=0.3, freq_end=120))  # revived
                elif delta < max_hp * 0.95:  # a full refill usually means a load/area change
                    heal = float(self._s("heal", 0.6))
                    mixer.play(synth.Tone(260, 0.45 * heal, 0.35, attack=0.08, release=0.2, freq_end=420))
                    mixer.play(synth.Tone(390, 0.3 * heal, 0.3, attack=0.1, release=0.18, freq_end=600), delay=0.08)
                    self._flash((0, 255, 90), now, 0.35)
        self.prev_hp = hp

        low = float(self._s("low_hp", 0.25))
        low_hp = in_play and 0 < hp and frac <= low
        if low_hp and self._s("heartbeat", True) and now >= self._next_beat:
            period = 0.55 + 0.65 * (frac / low)  # faster as HP falls
            synth.play_heartbeat(mixer, 0.6 + 0.4 * (1 - frac / low))
            self._next_beat = now + period

        self._mp(ctx, state, in_play)
        self._battle(ctx, state)

        # Triggers: jolt on damage, heartbeat when low, limp when dead.
        if self._s("trigger_effects", True) and in_play:
            if now < self._dead_until:
                out.left_trigger = out.right_trigger = TriggerEffect()
            elif now < self._jolt_until:
                jolt = TriggerEffect("vibration", {"position": 0, "amplitude": 7, "frequency": 45})
                out.left_trigger = out.right_trigger = jolt
            elif low_hp:
                beat = TriggerEffect("galloping", {"start": 0, "end": 9, "first_foot": 2, "second_foot": 4,
                                                   "frequency": 1 if frac > low / 2 else 2})
                out.left_trigger = out.right_trigger = beat

        if self._s("lightbar", "hp") == "hp":
            out.lightbar = self._light(frac, low_hp, now)
        if self._s("player_leds", True):
            leds = 0
            for i in range(5):
                if frac > i * 0.2 + 0.01:
                    leds |= 1 << i
            out.player_leds = leds
        return out

    def _on_death(self, mixer, now: float) -> None:
        mixer.play(synth.Tone(70, 1.0, 1.4, attack=0.01, release=0.9, freq_end=22))
        mixer.play(synth.impact(1.0, duration=0.5))
        self._flash((80, 0, 0), now, 1.5)
        self._dead_until = now + 2.0

    def _mp(self, ctx, state, in_play: bool) -> None:
        mp = state.get("mp")
        if mp is None or not self._s("mp", True) or not (0 <= mp <= 2000):
            return
        max_mp = state.get("max_mp") or 0
        self.max_mp_seen = max(self.max_mp_seen, mp, max_mp if 0 < max_mp <= 2000 else 0)
        if self.prev_mp is not None and in_play:
            if mp < self.prev_mp:  # spell cast
                ctx.mixer.play(synth.Tone(180, 0.5, 0.08, attack=0.002, release=0.05, freq_end=420))
            elif self.prev_mp == 0 and mp > 0 and mp >= self.max_mp_seen * 0.9:  # MP recharge finished
                ctx.mixer.play(synth.Tone(500, 0.35, 0.15, release=0.1))
                ctx.mixer.play(synth.Tone(660, 0.35, 0.2, release=0.15), delay=0.12)
        self.prev_mp = mp

    def _battle(self, ctx, state) -> None:
        battle = state.get("battle")
        if battle is None or not self._s("battle_alerts", True):
            return
        battle &= 3
        if self.prev_battle is not None and battle != self.prev_battle and battle > self.prev_battle:
            amp = 0.5 if battle == 1 else 0.9
            ctx.mixer.play(synth.Tone(110, amp, 0.07, attack=0.002, release=0.05))
            ctx.mixer.play(synth.Tone(110, amp, 0.07, attack=0.002, release=0.05), delay=0.14)
        self.prev_battle = battle

    def _flash(self, color, now: float, duration: float) -> None:
        self._flash_color, self._flash_until = color, now + duration

    def _light(self, frac: float, low_hp: bool, now: float) -> tuple[int, int, int]:
        if now < self._flash_until:
            return self._flash_color
        if low_hp:
            pulse = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self._t * 6.0))
            return (int(255 * pulse), 0, 0)
        frac = min(max(frac, 0.0), 1.0)
        if frac > 0.5:
            k = (frac - 0.5) / 0.5
            return (int(255 * (1 - k)), 220, int(40 * k))
        k = frac / 0.5
        return (255, int(200 * k), 0)
