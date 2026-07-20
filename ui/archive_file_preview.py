from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import PurePosixPath

from PySide6.QtCore import QFileInfo, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QListView,
    QListWidgetItem,
    QStackedWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    ListWidget,
    RoundMenu,
    TableWidget,
    TreeWidget,
)

PATH_ROLE = int(Qt.ItemDataRole.UserRole)
MAX_RENDERED_ENTRIES = 2_000


class ArchiveFilePreviewPanel(QWidget):
    """Reusable, bounded preview of files that live inside an archive."""

    entrySelected = Signal(str)
    entryActivated = Signal(str)
    entryExportRequested = Signal(str)
    entryOpenRequested = Signal(str)
    packageLocationRequested = Signal()

    def __init__(
        self,
        translate: Callable[[str], str],
        format_size: Callable[[int], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._t = translate
        self._format_size = format_size
        self._entries: list[tuple[str, int]] = []
        self._filtered_entries: list[tuple[str, int]] = []
        self._icon_provider = QFileIconProvider()
        self._allow_export = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.count_label = BodyLabel()
        self.count_label.setWordWrap(True)
        self.filter_input = LineEdit()
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.setMaximumWidth(260)
        self.filter_input.textChanged.connect(self._apply_filter)
        self.view_label = BodyLabel()
        self.view_combo = ComboBox()
        self.view_combo.addItems(["tree", "list", "table", "icons"])
        self.view_combo.currentIndexChanged.connect(self._change_view)
        toolbar.addWidget(self.count_label, 1)
        toolbar.addWidget(self.filter_input)
        toolbar.addWidget(self.view_label)
        toolbar.addWidget(self.view_combo)
        layout.addLayout(toolbar)

        self.stack = QStackedWidget()
        self.tree = TreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.tree.itemDoubleClicked.connect(self._tree_activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            lambda position: self._show_context_menu(self.tree, position)
        )
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        self.list_view = ListWidget()
        self.list_view.setAlternatingRowColors(True)
        self.list_view.itemSelectionChanged.connect(self._list_selection_changed)
        self.list_view.itemDoubleClicked.connect(self._list_activated)
        self.list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(
            lambda position: self._show_context_menu(self.list_view, position)
        )

        self.table = TableWidget()
        self.table.setColumnCount(3)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.currentCellChanged.connect(self._table_selection_changed)
        self.table.cellDoubleClicked.connect(self._table_activated)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda position: self._show_context_menu(self.table, position)
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )

        self.icon_view = ListWidget()
        self.icon_view.setViewMode(QListView.ViewMode.IconMode)
        self.icon_view.setIconSize(QSize(56, 56))
        self.icon_view.setGridSize(QSize(164, 126))
        self.icon_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.icon_view.setUniformItemSizes(True)
        self.icon_view.setWordWrap(True)
        self.icon_view.itemSelectionChanged.connect(self._list_selection_changed)
        self.icon_view.itemDoubleClicked.connect(self._list_activated)
        self.icon_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.icon_view.customContextMenuRequested.connect(
            lambda position: self._show_context_menu(self.icon_view, position)
        )

        for view in (self.tree, self.list_view, self.table, self.icon_view):
            view.setMinimumHeight(390)
            self.stack.addWidget(view)
        layout.addWidget(self.stack, 1)
        self.retranslate()

    def retranslate(self) -> None:
        self.filter_input.setPlaceholderText(self._t("filter_files"))
        self.view_label.setText(self._t("file_view"))
        current_index = self.view_combo.currentIndex()
        self.view_combo.blockSignals(True)
        self.view_combo.clear()
        self.view_combo.addItems(
            [
                self._t("view_tree"),
                self._t("view_list"),
                self._t("view_table"),
                self._t("view_icons"),
            ]
        )
        self.view_combo.setCurrentIndex(max(0, current_index))
        self.view_combo.blockSignals(False)
        self.tree.setHeaderLabels([self._t("name"), self._t("size")])
        self.table.setHorizontalHeaderLabels(
            [self._t("name"), self._t("folder_path"), self._t("size")]
        )
        self._update_count_label()

    def set_export_enabled(self, enabled: bool) -> None:
        self._allow_export = enabled

    def set_entries(self, entries: Iterable[object]) -> None:
        normalized: list[tuple[str, int]] = []
        for entry in entries:
            path = str(getattr(entry, "path", "")).replace("\\", "/").strip("/")
            if not path:
                continue
            try:
                size = int(getattr(entry, "size", 0))
            except (TypeError, ValueError):
                size = 0
            normalized.append((path, max(0, size)))
        self._entries = normalized
        self._apply_filter()

    def current_entry_path(self) -> str | None:
        view = self.stack.currentWidget()
        if view is self.tree:
            item = self.tree.currentItem()
            return self._item_path(item)
        if view is self.table:
            item = self.table.item(self.table.currentRow(), 0)
            return self._item_path(item)
        item = view.currentItem()
        return self._item_path(item)

    def _apply_filter(self) -> None:
        query = self.filter_input.text().strip().casefold()
        entries = [entry for entry in self._entries if not query or query in entry[0].casefold()]
        self._filtered_entries = entries[:MAX_RENDERED_ENTRIES]
        self._populate_views()
        self._update_count_label()

    def _update_count_label(self) -> None:
        total = len(self._entries)
        shown = len(self._filtered_entries)
        if total > shown:
            self.count_label.setText(self._t("preview_showing", shown=shown, total=total))
        else:
            self.count_label.setText(self._t("file_count") + f": {total}")

    def _populate_views(self) -> None:
        for view in (self.tree, self.list_view, self.table, self.icon_view):
            view.setUpdatesEnabled(False)
        try:
            self._populate_tree()
            self._populate_list(self.list_view, icons=False)
            self._populate_table()
            self._populate_list(self.icon_view, icons=True)
        finally:
            for view in (self.tree, self.list_view, self.table, self.icon_view):
                view.setUpdatesEnabled(True)

    def _populate_tree(self) -> None:
        self.tree.clear()
        directories: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for path, size in self._filtered_entries:
            parts = PurePosixPath(path).parts
            parent: QTreeWidgetItem | None = None
            for depth, part in enumerate(parts[:-1], start=1):
                key = parts[:depth]
                folder = directories.get(key)
                if folder is None:
                    folder = QTreeWidgetItem([part, ""])
                    folder.setToolTip(0, "/".join(key))
                    if parent is None:
                        self.tree.addTopLevelItem(folder)
                    else:
                        parent.addChild(folder)
                    directories[key] = folder
                parent = folder
            file_item = QTreeWidgetItem([parts[-1], self._format_size(size)])
            file_item.setData(0, PATH_ROLE, path)
            file_item.setToolTip(0, path)
            if parent is None:
                self.tree.addTopLevelItem(file_item)
            else:
                parent.addChild(file_item)
        self.tree.expandToDepth(0)

    def _populate_list(self, view: ListWidget, *, icons: bool) -> None:
        view.clear()
        for path, size in self._filtered_entries:
            file_name = PurePosixPath(path).name
            text = (
                f"{file_name}\n{self._format_size(size)}"
                if icons
                else f"{path}  ({self._format_size(size)})"
            )
            item = QListWidgetItem(text)
            item.setData(PATH_ROLE, path)
            item.setToolTip(path)
            if icons:
                item.setIcon(self._icon_provider.icon(QFileInfo(file_name)))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            view.addItem(item)

    def _populate_table(self) -> None:
        self.table.clearContents()
        self.table.setRowCount(len(self._filtered_entries))
        for row, (path, size) in enumerate(self._filtered_entries):
            pure_path = PurePosixPath(path)
            values = [pure_path.name, str(pure_path.parent), self._format_size(size)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(PATH_ROLE, path)
                item.setToolTip(path)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, column, item)

    def _change_view(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    @staticmethod
    def _item_path(item: object) -> str | None:
        if item is None:
            return None
        try:
            # QTreeWidgetItem keeps data per column, unlike list/table items.
            # Qt can emit selection changes while a tree is being cleared during
            # shutdown, so keep this lookup deliberately defensive.
            if isinstance(item, QTreeWidgetItem):
                path = item.data(0, PATH_ROLE)
            else:
                path = item.data(PATH_ROLE)
        except RuntimeError:
            return None
        return str(path) if path else None

    def _tree_selection_changed(self) -> None:
        path = self._item_path(self.tree.currentItem())
        if path:
            self.entrySelected.emit(path)

    def _list_selection_changed(self) -> None:
        view = self.stack.currentWidget()
        path = self._item_path(view.currentItem())
        if path:
            self.entrySelected.emit(path)

    def _table_selection_changed(self, row: int, *_: object) -> None:
        path = self._item_path(self.table.item(row, 0))
        if path:
            self.entrySelected.emit(path)

    def _tree_activated(self, item: QTreeWidgetItem, *_: object) -> None:
        path = self._item_path(item)
        if path:
            self.entryActivated.emit(path)

    def _list_activated(self, item: QListWidgetItem) -> None:
        path = self._item_path(item)
        if path:
            self.entryActivated.emit(path)

    def _table_activated(self, row: int, *_: object) -> None:
        path = self._item_path(self.table.item(row, 0))
        if path:
            self.entryActivated.emit(path)

    def _show_context_menu(self, view: QWidget, position) -> None:
        if not self._allow_export:
            return
        item = view.itemAt(position) if hasattr(view, "itemAt") else None
        path = self._item_path(item)
        if not path:
            return
        menu = RoundMenu(parent=self)
        extract_action = Action(FluentIcon.SAVE, self._t("extract_selected_entry"))
        extract_action.triggered.connect(lambda: self.entryExportRequested.emit(path))
        open_action = Action(FluentIcon.PLAY, self._t("open_with_system_default"))
        open_action.triggered.connect(lambda: self.entryOpenRequested.emit(path))
        location_action = Action(FluentIcon.FOLDER, self._t("open_package_location"))
        location_action.triggered.connect(self.packageLocationRequested.emit)
        menu.addAction(extract_action)
        menu.addAction(open_action)
        menu.addSeparator()
        menu.addAction(location_action)
        menu.exec(view.mapToGlobal(position))
