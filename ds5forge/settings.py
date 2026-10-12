"""Persistent app settings (%APPDATA%\\DS5Forge\\settings.json)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields

from .profiles import DEFAULT_PROFILE_NAME, config_dir

log = logging.getLogger(__name__)

HAPTICS_MODES = ("auto", "rumble", "off")


@dataclass
class Settings:
    haptics_mode: str = "auto"  # auto = HD haptics when possible, else rumble
    bt_haptics: bool = True  # use experimental HD haptics over Bluetooth
    haptic_intensity: float = 1.0  # 0-2
    trigger_intensity: float = 1.0  # 0-1
    audio_capture: bool = True  # listen to game audio for audio haptics
    auto_profiles: bool = True  # switch profile when a known game is running
    profile: str = DEFAULT_PROFILE_NAME  # profile used when no game is detected / manual choice
    lightbar_color: list[int] = field(default_factory=lambda: [0, 90, 255])
    led_brightness: int = 0  # 0 bright, 1 medium, 2 dim

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except Exception as exc:
            log.warning("Could not read settings (%s); using defaults", exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        path = config_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
