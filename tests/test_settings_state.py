from pathlib import Path

from core.logging_config import runtime_log_directory
from ui import settings_state
from ui.settings_state import AppSettings


def test_developer_mode_is_disabled_by_default() -> None:
    assert AppSettings().developer_mode is False


def test_runtime_log_saving_is_disabled_by_default() -> None:
    assert AppSettings().persist_runtime_logs is False


def test_runtime_log_directory_uses_the_configured_temporary_root(tmp_path: Path) -> None:
    assert runtime_log_directory(tmp_path) == tmp_path / "backUpHelper" / "logs"


def test_save_settings_does_not_raise_when_its_destination_is_unwritable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings_state, "settings_path", lambda: tmp_path)

    assert settings_state.save_settings(AppSettings()) is False


def test_settings_default_to_the_application_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_state, "application_directory", lambda: tmp_path)

    assert settings_state.settings_path() == tmp_path / "settings.json"


def test_settings_directory_can_be_changed_and_reset(monkeypatch, tmp_path: Path) -> None:
    application_dir = tmp_path / "app"
    custom_dir = tmp_path / "custom"
    monkeypatch.setattr(settings_state, "application_directory", lambda: application_dir)
    settings = AppSettings(theme="dark")

    assert settings_state.set_settings_directory(settings, custom_dir)
    assert settings_state.settings_path() == custom_dir / "settings.json"
    assert settings_state.load_settings().theme == "dark"

    assert settings_state.reset_settings_directory(settings)
    assert settings_state.settings_path() == application_dir / "settings.json"
    assert settings_state.load_settings().theme == "dark"
