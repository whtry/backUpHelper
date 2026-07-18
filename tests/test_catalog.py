from __future__ import annotations

from adapters.catalog import discover_backup_items


def test_catalog_marks_sensitive_items_not_selected_by_default() -> None:
    items = discover_backup_items(include_missing=True)
    sensitive = [item for item in items if item.sensitive]
    assert sensitive
    assert all(not item.default_selected for item in sensitive)


def test_catalog_contains_required_first_wave_adapters() -> None:
    ids = {item.id for item in discover_backup_items(include_missing=True)}
    assert {"ssh", "gitconfig", "condarc", "vscode-user", "wechat-files", "qq-files"} <= ids
