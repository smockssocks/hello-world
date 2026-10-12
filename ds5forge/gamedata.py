"""Versioned memory maps for games we read state from.

Built-in maps live in ``ds5forge/gamedata/<id>.json``. Values you find with the
Find-a-value wizard are saved to ``%APPDATA%\\DS5Forge\\gamedata\\<id>.json`` and
merged on top, so a game update or a missing address never needs a new release.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .memory import Field, Memory, MemoryError_
from .profiles import config_dir

log = logging.getLogger(__name__)


def _builtin_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "ds5forge" / "gamedata"


def user_dir() -> Path:
    path = config_dir() / "gamedata"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _int(v) -> int:
    return int(v, 0) if isinstance(v, str) else int(v)


@dataclass
class Version:
    name: str
    check: dict[str, Any] | None
    fields: dict[str, Field] = field(default_factory=dict)

    def matches(self, mem: Memory) -> bool:
        c = self.check
        if not c:
            return False
        try:
            if "module_size" in c:
                return mem.size == _int(c["module_size"])
            value = mem.read_value(_int(c["offset"]), c.get("type", "u8"))
            if "equals" in c:
                return value == c["equals"]
            if "string" in c:
                raw = mem.read(mem.base + _int(c["offset"]), len(c["string"]))
                return raw.decode("latin-1") == c["string"]
        except (MemoryError_, OSError):
            return False
        return False


@dataclass
class GameData:
    id: str
    name: str
    process: list[str]
    versions: dict[str, Version]
    source: str = ""

    def detect(self, mem: Memory) -> Version | None:
        for v in self.versions.values():
            if v.matches(mem):
                return v
        return None


def _parse_fields(d: dict) -> dict[str, Field]:
    out = {}
    for name, f in (d or {}).items():
        ptr = f.get("pointer")
        out[name] = Field(name, _int(f["offset"]), f.get("type", "u32"),
                          [_int(x) for x in ptr] if ptr else None)
    return out


def _merge(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, val in extra.items():
        if key == "versions":
            versions = {k: dict(v) for k, v in base.get("versions", {}).items()}
            for vname, vdata in val.items():
                cur = versions.setdefault(vname, {})
                if "check" in vdata:
                    cur["check"] = vdata["check"]
                cur["fields"] = {**cur.get("fields", {}), **vdata.get("fields", {})}
            merged["versions"] = versions
        else:
            merged[key] = val
    return merged


def load(game_id: str) -> GameData | None:
    raw: dict = {}
    builtin = _builtin_dir() / (game_id + ".json")
    if builtin.exists():
        raw = json.loads(builtin.read_text(encoding="utf-8"))
    user = user_dir() / (game_id + ".json")
    if user.exists():
        try:
            raw = _merge(raw, json.loads(user.read_text(encoding="utf-8")))
        except Exception as exc:
            log.warning("Ignoring %s: %s", user, exc)
    if not raw:
        return None
    versions = {name: Version(name, v.get("check"), _parse_fields(v.get("fields", {})))
                for name, v in raw.get("versions", {}).items()}
    return GameData(raw.get("id", game_id), raw.get("name", game_id), raw.get("process", []),
                    versions, raw.get("source", ""))


def all_games() -> list[GameData]:
    ids = {p.stem for p in _builtin_dir().glob("*.json")} | {p.stem for p in user_dir().glob("*.json")}
    return [g for g in (load(i) for i in sorted(ids)) if g is not None]


def save_user_field(game: GameData, mem: Memory, version: Version | None, name: str,
                    offset: int, kind: str) -> str:
    """Store a field found with the wizard. Returns the version name it was saved under."""
    path = user_dir() / (game.id + ".json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"id": game.id}
    if version is None:
        # Unknown build: identify it by the size of the game's executable image.
        vname = "My version (image size 0x%X)" % mem.size
        check = {"module_size": hex(mem.size)}
    else:
        vname, check = version.name, None
    vdata = data.setdefault("versions", {}).setdefault(vname, {})
    if check:
        vdata["check"] = check
    vdata.setdefault("fields", {})[name] = {"offset": hex(offset), "type": kind}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return vname


def kind_for_size(size: int, signed: bool = False) -> str:
    return {1: "u8", 2: "i16" if signed else "u16", 4: "i32" if signed else "u32"}[size]

