from __future__ import annotations

import json
import locale
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.paths import appdata_roaming


def default_language() -> str:
    language = (locale.getlocale()[0] or "").lower()
    if language.startswith("en"):
        return "en_US"
    if language.startswith("zh") and (
        "cn" in language or "hans" in language or "simplified" in language
    ):
        return "zh_CN"
    return "zh_CN"


@dataclass
class AppSettings:
    language: str = field(default_factory=default_language)
    theme: str = "auto"
    encrypt_by_default: bool = False
    sensitive_confirm: bool = True
    window_geometry: str | None = None
    window_state: str | None = None
    current_page: str = "smartBackupPage"


def settings_path() -> Path:
    return appdata_roaming() / "backUpHelper" / "settings.json"


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.exists():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    return AppSettings(**{**asdict(AppSettings()), **data})


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
