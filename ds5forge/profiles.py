"""Per-game profiles: which integration to run, haptic layers, and static/conditional trigger effects.

Built-in profiles live in ``ds5forge/profiles/*.json``. Your own profiles go in
``%APPDATA%\\DS5Forge\\profiles`` and override built-ins with the same name.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import InputState
from .triggers import TriggerEffect

log = logging.getLogger(__name__)


def _builtin_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "ds5forge" / "profiles"


def config_dir() -> Path:
    root = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    path = Path(root) / "DS5Forge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_profile_dir() -> Path:
    path = config_dir() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class TriggerRule:
    """``when`` lists inputs that must all be held (e.g. ["l2"] = while aiming)."""

    when: list[str]
    left: TriggerEffect | None = None
    right: TriggerEffect | None = None

    def matches(self, inp: InputState) -> bool:
        return all(inp.pressed(name) for name in self.when)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TriggerRule":
        when = d.get("when", [])
        if isinstance(when, str):
            when = [when]
        return cls(
            when=[w.lower() for w in when],
            left=TriggerEffect.from_dict(d["left"]) if d.get("left") else None,
            right=TriggerEffect.from_dict(d["right"]) if d.get("right") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"when": self.when}
        if self.left:
            d["left"] = self.left.to_dict()
        if self.right:
            d["right"] = self.right.to_dict()
        return d


@dataclass
class Profile:
    name: str
    exe: list[str] = field(default_factory=list)
    notes: str = ""
    integration: str | None = None
    integration_settings: dict[str, Any] = field(default_factory=dict)
    audio_haptics: bool = True
    audio_gain: float = 1.0
    audio_cutoff: float = 250.0
    audio_trigger_kick: bool = False
    audio_kick_threshold: float = 0.15
    xbox_bridge: bool = False
    xbox_trigger_rumble: bool = False
    left_trigger: TriggerEffect | None = None
    right_trigger: TriggerEffect | None = None
    rules: list[TriggerRule] = field(default_factory=list)
    lightbar: tuple[int, int, int] | None = None
    builtin: bool = False
    path: Path | None = None

    def triggers_for(self, inp: InputState) -> tuple[TriggerEffect | None, TriggerEffect | None]:
        left, right = self.left_trigger, self.right_trigger
        for rule in self.rules:
            if rule.matches(inp):
                left = rule.left or left
                right = rule.right or right
                break
        return left, right

    def matches_exe(self, running: set[str]) -> bool:
        return any(e.lower() in running for e in self.exe)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        audio = d.get("audio_haptics", {})
        if isinstance(audio, bool):
            audio = {"enabled": audio}
        xbox = d.get("xbox_bridge", {})
        if isinstance(xbox, bool):
            xbox = {"enabled": xbox}
        triggers = d.get("triggers", {})
        lb = d.get("lightbar")
        return cls(
            name=d["name"],
            exe=list(d.get("exe", [])),
            notes=d.get("notes", ""),
            integration=d.get("integration"),
            integration_settings=dict(d.get("integration_settings", {})),
            audio_haptics=audio.get("enabled", True),
            audio_gain=float(audio.get("gain", 1.0)),
            audio_cutoff=float(audio.get("cutoff", 250.0)),
            audio_trigger_kick=audio.get("trigger_kick", False),
            audio_kick_threshold=float(audio.get("kick_threshold", 0.15)),
            xbox_bridge=xbox.get("enabled", False),
            xbox_trigger_rumble=xbox.get("trigger_rumble", False),
            left_trigger=TriggerEffect.from_dict(triggers["left"]) if triggers.get("left") else None,
            right_trigger=TriggerEffect.from_dict(triggers["right"]) if triggers.get("right") else None,
            rules=[TriggerRule.from_dict(r) for r in d.get("rules", [])],
            lightbar=tuple(lb) if lb else None,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "exe": self.exe}
        if self.notes:
            d["notes"] = self.notes
        if self.integration:
            d["integration"] = self.integration
        if self.integration_settings:
            d["integration_settings"] = self.integration_settings
        d["audio_haptics"] = {"enabled": self.audio_haptics, "gain": self.audio_gain,
                              "cutoff": self.audio_cutoff, "trigger_kick": self.audio_trigger_kick,
                              "kick_threshold": self.audio_kick_threshold}
        d["xbox_bridge"] = {"enabled": self.xbox_bridge, "trigger_rumble": self.xbox_trigger_rumble}
        triggers = {}
        if self.left_trigger:
            triggers["left"] = self.left_trigger.to_dict()
        if self.right_trigger:
            triggers["right"] = self.right_trigger.to_dict()
        if triggers:
            d["triggers"] = triggers
        if self.rules:
            d["rules"] = [r.to_dict() for r in self.rules]
        if self.lightbar:
            d["lightbar"] = list(self.lightbar)
        return d


DEFAULT_PROFILE_NAME = "Default (any game)"


def load_profiles(extra_dirs: list[Path] | None = None) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    dirs = [(_builtin_dir(), True), (user_profile_dir(), False)] + [(d, False) for d in (extra_dirs or [])]
    for directory, builtin in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                prof = Profile.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                log.warning("Skipping profile %s: %s", path.name, exc)
                continue
            prof.builtin, prof.path = builtin, path
            profiles[prof.name] = prof
    if DEFAULT_PROFILE_NAME not in profiles:
        profiles[DEFAULT_PROFILE_NAME] = Profile(name=DEFAULT_PROFILE_NAME, builtin=True)
    return profiles


def save_user_profile(profile: Profile) -> Path:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in profile.name).strip() or "profile"
    path = user_profile_dir() / (safe + ".json")
    path.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
    profile.path, profile.builtin = path, False
    return path


def running_processes() -> set[str]:
    try:
        import psutil
    except ImportError:
        return set()
    names = set()
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name")
        if name:
            names.add(name.lower())
    return names
