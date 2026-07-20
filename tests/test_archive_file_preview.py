from __future__ import annotations

import pytest


def test_tree_item_path_reads_the_user_role_from_column_zero() -> None:
    pytest.importorskip("PySide6")
    pytest.importorskip("qfluentwidgets")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidgetItem

    from ui.archive_file_preview import ArchiveFilePreviewPanel

    item = QTreeWidgetItem(["example.txt", "1 KiB"])
    item.setData(0, int(Qt.ItemDataRole.UserRole), "data/example.txt")

    assert ArchiveFilePreviewPanel._item_path(item) == "data/example.txt"
