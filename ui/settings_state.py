from __future__ import annotations

import json
import locale
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.paths import appdata_roaming

SETTINGS_FILE_NAME = "settings.json"
SETTINGS_LOCATION_FILE_NAME = ".back_up_helper_config_location.json"


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
    developer_mode: bool = False
    encrypt_by_default: bool = False
    sensitive_confirm: bool = True
    temporary_root: str | None = None
    persist_runtime_logs: bool = False
    window_geometry: str | None = None
    window_state: str | None = None
    current_page: str = "smartBackupPage"


def application_directory() -> Path:
    """Return the folder containing the executable, or the project root in development."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def default_settings_path() -> Path:
    return application_directory() / SETTINGS_FILE_NAME


def settings_location_path() -> Path:
    return application_directory() / SETTINGS_LOCATION_FILE_NAME


def _configured_settings_path() -> Path | None:
    location_path = settings_location_path()
    try:
        data = json.loads(location_path.read_text(encoding="utf-8"))
        value = data.get("settings_path")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
    return candidate if candidate.is_absolute() else location_path.parent / candidate


def settings_path() -> Path:
    return _configured_settings_path() or default_settings_path()


def settings_directory() -> Path:
    return settings_path().parent


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.exists():
        # Preserve settings from older releases until the next successful save.
        legacy_path = appdata_roaming() / "backUpHelper" / SETTINGS_FILE_NAME
        path = legacy_path if path == default_settings_path() and legacy_path.exists() else path
    if not path.exists():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    defaults = asdict(AppSettings())
    saved_values = {key: value for key, value in data.items() if key in defaults}
    return AppSettings(**{**defaults, **saved_values})


def _write_settings(settings: AppSettings, path: Path) -> bool:
    temporary_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(path)
    except OSError as exc:
        logging.getLogger("backUpHelper").warning(
            "Unable to save application settings to %s: %s", path, exc
        )
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _write_location(path: Path) -> bool:
    location_path = settings_location_path()
    temporary_path = location_path.with_suffix(".tmp")
    try:
        location_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps({"settings_path": str(path)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(location_path)
    except OSError as exc:
        logging.getLogger("backUpHelper").warning(
            "Unable to save settings location to %s: %s", location_path, exc
        )
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def save_settings(settings: AppSettings) -> bool:
    return _write_settings(settings, settings_path())


def set_settings_directory(settings: AppSettings, directory: Path) -> bool:
    destination = directory.expanduser().resolve() / SETTINGS_FILE_NAME
    default_path = default_settings_path()
    if destination == default_path:
        if not _write_settings(settings, default_path):
            return False
        return _clear_location_file()
    if not _write_settings(settings, destination):
        return False
    return _write_location(destination)


def reset_settings_directory(settings: AppSettings) -> bool:
    if not _write_settings(settings, default_settings_path()):
        return False
    return _clear_location_file()


def _clear_location_file() -> bool:
    location_path = settings_location_path()
    try:
        location_path.unlink(missing_ok=True)
    except OSError as exc:
        logging.getLogger("backUpHelper").warning(
            "Unable to reset settings location at %s: %s", location_path, exc
        )
        return False
    return True
