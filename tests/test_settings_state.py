from ui.settings_state import AppSettings


def test_developer_mode_is_disabled_by_default() -> None:
    assert AppSettings().developer_mode is False
