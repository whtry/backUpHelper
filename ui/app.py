from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from adapters.catalog import discover_backup_items
from backup.full_backup import FullBackupTarget, create_folder_backup, create_volume_iso_backup
from backup.package import (
    BackupSpaceCheck,
    check_backup_space,
    create_backup_package,
    estimate_backup_space,
    format_bytes,
)
from backup.selection_preview import preview_item_files
from backup.volumes import list_windows_volumes
from core.cancellation import OperationCancelledError
from core.logging_config import configure_application_logging, runtime_log_directory
from core.models import ArchiveFormat, BackupItem
from core.temporary import prepare_temporary_root
from inventory.icons import normalize_icon_path
from inventory.installed_apps import list_installed_applications
from preview.file_preview import IMAGE_EXTENSIONS, preview_entry_text
from preview.package_reader import (
    copy_entry_to,
    extract_package_to,
    list_entries,
    read_entry_bytes,
    read_manifest,
)
from restore.executor import execute_restore_plan
from restore.planner import build_restore_plan
from ui.i18n import SUPPORTED_LANGUAGES, tr
from ui.settings_state import (
    load_settings,
    reset_settings_directory,
    save_settings,
    set_settings_directory,
    settings_directory,
)


def _missing_ui_message() -> int:
    print(
        "UI dependencies are not installed. Install with: "
        'python -m pip install -e ".[ui]"',
        file=sys.stderr,
    )
    return 2


def run_app(auto_quit_ms: int | None = None) -> int:
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import (
            QByteArray,
            QEvent,
            QFileInfo,
            QObject,
            QSize,
            Qt,
            QThread,
            QTimer,
            QUrl,
            Signal,
            Slot,
            qVersion,
        )
        from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QAbstractScrollArea,
            QApplication,
            QButtonGroup,
            QFileDialog,
            QFileIconProvider,
            QGridLayout,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QListView,
            QListWidgetItem,
            QProgressBar,
            QSizePolicy,
            QStackedWidget,
            QTableWidgetItem,
            QTreeWidgetItem,
            QVBoxLayout,
            QWidget,
        )
        from qfluentwidgets import (
            Action,
            BodyLabel,
            CaptionLabel,
            CheckBox,
            ComboBox,
            FluentIcon,
            FluentTranslator,
            FluentWindow,
            LineEdit,
            ListWidget,
            MessageBox,
            NavigationItemPosition,
            PasswordLineEdit,
            PrimaryPushButton,
            PushButton,
            RoundMenu,
            ScrollArea,
            SimpleCardWidget,
            SubtitleLabel,
            TableWidget,
            TextEdit,
            Theme,
            TitleLabel,
            TreeWidget,
            setTheme,
            setThemeColor,
        )

        from ui.archive_file_preview import ArchiveFilePreviewPanel
    except ImportError:
        return _missing_ui_message()

    settings = load_settings()
    configured_log_root = Path(settings.temporary_root) if settings.temporary_root else None
    configure_application_logging(settings.persist_runtime_logs, configured_log_root)
    logger = logging.getLogger("backUpHelper")
    logger.info("Starting backUpHelper UI")
    logger.info("Qt UI environment | PySide6=%s | Qt=%s", pyside_version, qVersion())
    PATH_ROLE = int(Qt.ItemDataRole.UserRole)
    ITEM_ID_ROLE = PATH_ROLE + 1
    RELATIVE_PATH_ROLE = PATH_ROLE + 2
    DUMMY_NODE_ROLE = PATH_ROLE + 3

    def t(key: str, **kwargs: object) -> str:
        return tr(settings.language, key, **kwargs)

    def icon(name: str, fallback: str = "APPLICATION"):
        for candidate in (name, fallback, "HOME", "SETTING"):
            value = getattr(FluentIcon, candidate, None)
            if value is not None:
                return value
        return None

    def password_or_none(checkbox: CheckBox, password_input: PasswordLineEdit) -> str | None:
        if not checkbox.isChecked():
            return None
        password = password_input.text()
        if not password:
            return ""
        return password

    def default_package_name(prefix: str, archive_format: ArchiveFormat) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        suffix = "." + archive_format.value if archive_format != ArchiveFormat.DIRECTORY else ""
        return f"{prefix}-{timestamp}{suffix}"

    def archive_format_from_path(path: Path, fallback: ArchiveFormat) -> ArchiveFormat:
        suffix = path.suffix.lower()
        if suffix == ".zip":
            return ArchiveFormat.ZIP
        if suffix == ".7z":
            return ArchiveFormat.SEVEN_Z
        if suffix == ".iso":
            return ArchiveFormat.ISO
        return fallback

    def save_package_path(
        parent: QWidget,
        title: str,
        default_name: str,
        formats: tuple[ArchiveFormat, ...],
    ) -> tuple[Path, ArchiveFormat] | None:
        suffixes = {
            ArchiveFormat.ZIP: "*.zip",
            ArchiveFormat.SEVEN_Z: "*.7z",
            ArchiveFormat.ISO: "*.iso",
        }
        filters = [
            f"{fmt.value.upper()} ({suffixes[fmt]})"
            for fmt in formats
            if fmt != ArchiveFormat.DIRECTORY
        ]
        file_name, selected_filter = QFileDialog.getSaveFileName(
            parent,
            title,
            default_name,
            ";;".join(filters + ["All files (*.*)"]),
        )
        if not file_name:
            return None
        path = Path(file_name)
        selected_format = archive_format_from_path(path, formats[0])
        if selected_format not in formats:
            selected_format = formats[0]
        if not path.suffix:
            for fmt in formats:
                if fmt.value.upper() in selected_filter:
                    selected_format = fmt
                    break
        if selected_format != ArchiveFormat.DIRECTORY and not path.suffix:
            path = path.with_suffix("." + selected_format.value)
        return path, selected_format

    def asset_path(name: str) -> Path:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root) / "assets" / name
        return Path(__file__).resolve().parents[1] / "assets" / name

    def app_icon_path() -> Path:
        return asset_path("app-icon.svg")

    def open_url(url: str) -> None:
        logger.info("Opening external URL: %s", url)
        QDesktopServices.openUrl(QUrl(url))

    def open_local_path(path: Path, action: str) -> None:
        logger.info("%s: %s", action, path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def apply_theme(value: str) -> None:
        theme = {
            "auto": Theme.AUTO,
            "light": Theme.LIGHT,
            "dark": Theme.DARK,
        }[value]
        setTheme(theme)

    def configured_temporary_root() -> Path | None:
        return Path(settings.temporary_root) if settings.temporary_root else None

    def show_backup_created(parent: QWidget, package: object) -> None:
        package_path = Path(str(package))
        dialog = MessageBox(t("backup_created"), str(package_path), parent)
        dialog.yesButton.setText(t("open_containing_folder"))
        dialog.cancelButton.setText(t("close"))
        if dialog.exec():
            open_local_path(package_path.parent, "Opening backup output directory")

    def confirm_stop_operation(parent: QWidget) -> bool:
        dialog = MessageBox(t("stop_operation"), t("stop_operation_body"), parent)
        dialog.yesButton.setText(t("stop"))
        dialog.cancelButton.setText(t("cancel"))
        return bool(dialog.exec())

    def apply_app_style(app: QApplication) -> None:
        font = QFont("Microsoft YaHei UI")
        font.setPointSize(11)
        app.setFont(font)
        setThemeColor("#0078D4")
        apply_theme(settings.theme)
        app.setStyleSheet(
            """
            QWidget {
                font-family: "Microsoft YaHei UI";
                font-size: 14px;
            }
            QTableWidget {
                gridline-color: transparent;
                selection-background-color: rgba(0, 120, 212, 54);
                selection-color: palette(text);
            }
            QHeaderView::section {
                min-height: 36px;
                padding: 8px;
                font-weight: 600;
            }
            QListWidget::item {
                min-height: 30px;
                padding: 6px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QWidget#pageContent {
                background: transparent;
            }
            """
        )

    def make_table_columns_resizable(table: TableWidget, fixed_first: int | None = None) -> None:
        header = table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(72)
        for column in range(table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        if fixed_first is not None:
            table.setColumnWidth(0, fixed_first)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        if hasattr(table, "setViewportUpdateMode"):
            table.setViewportUpdateMode(
                QAbstractScrollArea.ViewportUpdateMode.MinimalViewportUpdate
            )

    class InstalledAppsWorker(QObject):
        finished = Signal(object)
        failed = Signal(str)

        @Slot()
        def run(self) -> None:
            try:
                self.finished.emit(list_installed_applications())
            except Exception as exc:
                self.failed.emit(str(exc))

    class PreviewFilesWorker(QObject):
        finished = Signal(int, object)
        failed = Signal(int, str)

        def __init__(
            self,
            row: int,
            item: BackupItem,
            excluded_relative_paths: set[str],
            included_relative_paths: set[str] | None,
        ) -> None:
            super().__init__()
            self.row = row
            self.item = item
            self.excluded_relative_paths = set(excluded_relative_paths)
            self.included_relative_paths = (
                set(included_relative_paths) if included_relative_paths is not None else None
            )

        @Slot()
        def run(self) -> None:
            try:
                files = preview_item_files(
                    self.item,
                    limit=300,
                    excluded_relative_paths=self.excluded_relative_paths,
                    included_relative_paths=self.included_relative_paths,
                )
            except Exception as exc:
                self.failed.emit(self.row, str(exc))
                return
            self.finished.emit(self.row, files)

    class InteractionLogFilter(QObject):
        """Records deliberate mouse interactions while persistent logging is enabled."""

        def eventFilter(self, watched, event) -> bool:
            if not settings.persist_runtime_logs:
                return False
            event_type = event.type()
            if event_type not in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
                return False
            description = self._describe_widget(watched)
            if description:
                verb = "double-click" if event_type == QEvent.Type.MouseButtonDblClick else "click"
                logger.info("UI %s: %s", verb, description)
            return False

        @staticmethod
        def _describe_widget(watched) -> str | None:
            widget = watched if isinstance(watched, QWidget) else None
            while widget is not None:
                text = getattr(widget, "text", None)
                value = text() if callable(text) else ""
                if isinstance(value, str) and value.strip():
                    return f"{widget.metaObject().className()} [{value.strip()[:160]}]"
                object_name = widget.objectName()
                if object_name:
                    return f"{widget.metaObject().className()} [{object_name}]"
                widget = widget.parentWidget()
            return None

    class BackupJobWorker(QObject):
        progress = Signal(str, int, int)
        finished = Signal(object)
        failed = Signal(str)
        cancelled = Signal(str)

        def __init__(self, task) -> None:
            super().__init__()
            self.task = task
            self.cancel_event = Event()

        def cancel(self) -> None:
            self.cancel_event.set()

        @Slot()
        def run(self) -> None:
            try:
                result = self.task(self.progress.emit, self.cancel_event)
            except OperationCancelledError as exc:
                self.cancelled.emit(str(exc))
                return
            except Exception as exc:
                self.failed.emit(str(exc))
                return
            self.finished.emit(result)

    def is_excluded_relative(relative_path: str, excluded_paths: set[str]) -> bool:
        normalized = relative_path.strip("/")
        return any(
            normalized == excluded or normalized.startswith(f"{excluded}/")
            for excluded in excluded_paths
        )

    def has_excluded_child(relative_path: str, excluded_paths: set[str]) -> bool:
        normalized = relative_path.strip("/")
        if not normalized:
            return bool(excluded_paths)
        return any(excluded.startswith(f"{normalized}/") for excluded in excluded_paths)

    def has_included_child(relative_path: str, included_paths: set[str]) -> bool:
        normalized = relative_path.strip("/")
        if not normalized:
            return bool(included_paths)
        return any(included.startswith(f"{normalized}/") for included in included_paths)

    class Page(ScrollArea):
        def __init__(self, title_key: str, subtitle_key: str) -> None:
            super().__init__()
            self.title_key = title_key
            self.subtitle_key = subtitle_key
            self._wheel_trap_views = {}
            self.content = QWidget()
            self.content.setObjectName("pageContent")
            self.setWidget(self.content)
            self.setWidgetResizable(True)
            self.setFrameShape(ScrollArea.NoFrame)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.verticalScrollBar().setSingleStep(22)
            self.setStyleSheet("QScrollArea{background: transparent; border: none;}")
            self.viewport().setStyleSheet("background: transparent;")

            self.root_layout = QVBoxLayout(self.content)
            self.root_layout.setContentsMargins(36, 30, 36, 36)
            self.root_layout.setSpacing(20)
            self.title_label = TitleLabel()
            self.subtitle_label = BodyLabel()
            self.subtitle_label.setWordWrap(True)
            self.root_layout.addWidget(self.title_label)
            self.root_layout.addWidget(self.subtitle_label)
            self.retranslate()

        def add_card(self) -> tuple[SimpleCardWidget, QVBoxLayout]:
            card = SimpleCardWidget()
            card.setMinimumHeight(110)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(24, 22, 24, 22)
            layout.setSpacing(16)
            self.root_layout.addWidget(card)
            return card, layout

        def retranslate(self) -> None:
            self.title_label.setText(t(self.title_key))
            self.subtitle_label.setText(t(self.subtitle_key))

        def trap_child_wheel(self, *views) -> None:
            for view in views:
                viewport = view.viewport()
                self._wheel_trap_views[viewport] = view
                viewport.installEventFilter(self)

        def eventFilter(self, obj, event) -> bool:
            if event.type() == QEvent.Type.Wheel and obj in self._wheel_trap_views:
                view = self._wheel_trap_views[obj]
                scrollbar = view.verticalScrollBar()
                pixel_delta = event.pixelDelta().y()
                angle_delta = event.angleDelta().y()
                if pixel_delta:
                    scrollbar.setValue(scrollbar.value() - pixel_delta)
                elif angle_delta:
                    step = max(24, scrollbar.singleStep() * 3)
                    scrollbar.setValue(scrollbar.value() - int(angle_delta / 120 * step))
                return True
            return super().eventFilter(obj, event)

    class SmartBackupPage(Page):
        def __init__(self) -> None:
            super().__init__("smart_title", "smart_subtitle")
            self.items: list[BackupItem] = discover_backup_items()
            self.installed_apps = []
            self.selected_ids: set[str] = set()
            self.selected_app_names: set[str] = set()
            self.excluded_paths_by_item: dict[str, set[str]] = {}
            self.included_paths_by_item: dict[str, set[str]] = {}
            self.item_by_row: dict[int, BackupItem] = {}
            self.app_by_row = {}
            self.preview_paths: list[Path] = []
            self.loading_table = False
            self.loading_apps = False
            self.loading_preview_tree = False
            self.pending_preview_row: int | None = None
            self.queued_preview_row: int | None = None
            self.preview_thread: QThread | None = None
            self.preview_worker: PreviewFilesWorker | None = None
            self.apps_thread: QThread | None = None
            self.apps_worker: InstalledAppsWorker | None = None
            self.backup_thread: QThread | None = None
            self.backup_worker: BackupJobWorker | None = None
            self.preflight_thread: QThread | None = None
            self.preflight_worker: BackupJobWorker | None = None
            self.preflight_build_task = None
            self.select_all_apps_after_load = False
            self.preview_timer = QTimer(self)
            self.preview_timer.setSingleShot(True)
            self.preview_timer.timeout.connect(self.load_pending_file_preview)

            _, table_card_layout = self.add_card()
            header_row = QHBoxLayout()
            self.backup_items_label = SubtitleLabel()
            self.backup_select_all_button = PushButton(icon("ACCEPT_MEDIUM", "ACCEPT"), "")
            self.backup_select_all_button.setMaximumWidth(120)
            self.backup_select_all_button.clicked.connect(self.select_all_backup_items)
            self.backup_clear_button = PushButton(icon("CLEAR_SELECTION", "CANCEL"), "")
            self.backup_clear_button.setMaximumWidth(120)
            self.backup_clear_button.clicked.connect(self.clear_backup_items)
            self.selected_files_label = SubtitleLabel()
            self.item_filter_input = LineEdit()
            self.item_filter_input.setMaximumWidth(230)
            self.item_filter_input.setClearButtonEnabled(True)
            self.item_filter_input.textChanged.connect(self.filter_backup_items)
            header_row.addWidget(self.backup_items_label, 3)
            header_row.addWidget(self.item_filter_input)
            preview_header = QHBoxLayout()
            self.file_view_label = BodyLabel()
            self.file_view_combo = ComboBox()
            self.file_view_combo.addItems(["tree", "list", "table", "icons"])
            self.file_view_combo.currentIndexChanged.connect(self.change_file_preview_mode)
            self.preview_button = PushButton(icon("VIEW", "FOLDER"), "")
            self.preview_button.setMaximumWidth(150)
            self.preview_button.clicked.connect(self.load_current_file_preview)
            preview_header.addWidget(self.selected_files_label)
            preview_header.addStretch(1)
            preview_header.addWidget(self.file_view_label)
            preview_header.addWidget(self.file_view_combo)
            preview_header.addWidget(self.preview_button)
            header_row.addLayout(preview_header, 2)
            header_row.addWidget(self.backup_select_all_button)
            header_row.addWidget(self.backup_clear_button)
            table_card_layout.addLayout(header_row)

            split_row = QHBoxLayout()
            split_row.setSpacing(16)
            self.table = TableWidget()
            self.table.setColumnCount(6)
            self.table.verticalHeader().setVisible(False)
            self.table.setAlternatingRowColors(True)
            self.table.setWordWrap(False)
            self.table.setShowGrid(False)
            self.table.setMinimumHeight(420)
            self.table.setMaximumHeight(520)
            self.table.itemChanged.connect(self.on_item_changed)
            self.table.currentCellChanged.connect(self.on_current_item_changed)
            make_table_columns_resizable(self.table, 48)
            self.table.setColumnWidth(1, 220)
            self.table.setColumnWidth(2, 140)
            self.table.setColumnWidth(3, 110)
            self.table.setColumnWidth(4, 260)
            self.table.setColumnWidth(5, 320)
            self.table.setColumnWidth(0, 48)
            self.table.verticalHeader().setDefaultSectionSize(58)
            self.icon_provider = QFileIconProvider()
            self.file_preview_stack = QStackedWidget()
            self.file_preview_tree = TreeWidget()
            self.file_preview_tree.setMinimumWidth(360)
            self.file_preview_tree.setMinimumHeight(460)
            self.file_preview_tree.setHeaderHidden(False)
            self.file_preview_tree.itemChanged.connect(self.on_preview_tree_item_changed)
            self.file_preview_tree.itemExpanded.connect(self.on_preview_tree_item_expanded)
            self.file_preview_tree.itemDoubleClicked.connect(self.open_preview_tree_item)
            self.file_preview_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.file_preview_tree.customContextMenuRequested.connect(
                lambda position: self.show_preview_context_menu(self.file_preview_tree, position)
            )
            self.file_preview_list = ListWidget()
            self.file_preview_list.setMinimumWidth(360)
            self.file_preview_list.setMinimumHeight(460)
            self.file_preview_list.itemDoubleClicked.connect(self.open_preview_item)
            self.file_preview_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.file_preview_list.customContextMenuRequested.connect(
                lambda position: self.show_preview_context_menu(self.file_preview_list, position)
            )
            self.file_preview_table = TableWidget()
            self.file_preview_table.setColumnCount(4)
            self.file_preview_table.setWordWrap(False)
            self.file_preview_table.verticalHeader().setVisible(False)
            self.file_preview_table.setMinimumWidth(360)
            self.file_preview_table.setMinimumHeight(460)
            self.file_preview_table.itemDoubleClicked.connect(self.open_preview_table_item)
            self.file_preview_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.file_preview_table.customContextMenuRequested.connect(
                lambda position: self.show_preview_context_menu(self.file_preview_table, position)
            )
            self.file_preview_icons = ListWidget()
            self.file_preview_icons.setViewMode(QListView.ViewMode.IconMode)
            self.file_preview_icons.setIconSize(QSize(64, 64))
            self.file_preview_icons.setGridSize(QSize(190, 138))
            self.file_preview_icons.setResizeMode(QListView.ResizeMode.Adjust)
            self.file_preview_icons.setUniformItemSizes(True)
            self.file_preview_icons.setWordWrap(True)
            self.file_preview_icons.setMinimumWidth(360)
            self.file_preview_icons.setMinimumHeight(460)
            self.file_preview_icons.itemDoubleClicked.connect(self.open_preview_item)
            self.file_preview_icons.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.file_preview_icons.customContextMenuRequested.connect(
                lambda position: self.show_preview_context_menu(self.file_preview_icons, position)
            )
            self.file_preview_stack.addWidget(self.file_preview_tree)
            self.file_preview_stack.addWidget(self.file_preview_list)
            self.file_preview_stack.addWidget(self.file_preview_table)
            self.file_preview_stack.addWidget(self.file_preview_icons)
            self.trap_child_wheel(
                self.table,
                self.file_preview_tree,
                self.file_preview_list,
                self.file_preview_table,
                self.file_preview_icons,
            )
            split_row.addWidget(self.table, 3)
            split_row.addWidget(self.file_preview_stack, 2)
            table_card_layout.addLayout(split_row)
            self.populate_table()

            _, apps_layout = self.add_card()
            apps_header = QHBoxLayout()
            self.apps_label = SubtitleLabel()
            self.apps_summary_label = CaptionLabel()
            self.apps_filter_input = LineEdit()
            self.apps_filter_input.setMaximumWidth(230)
            self.apps_filter_input.setClearButtonEnabled(True)
            self.apps_filter_input.textChanged.connect(self.filter_apps)
            self.load_apps_button = PushButton(icon("SYNC", "APPLICATION"), "")
            self.load_apps_button.setMaximumWidth(170)
            self.load_apps_button.clicked.connect(self.load_installed_apps)
            self.apps_select_all_button = PushButton(icon("ACCEPT_MEDIUM", "ACCEPT"), "")
            self.apps_select_all_button.setMaximumWidth(120)
            self.apps_select_all_button.clicked.connect(self.select_all_apps)
            self.apps_clear_button = PushButton(icon("CLEAR_SELECTION", "CANCEL"), "")
            self.apps_clear_button.setMaximumWidth(120)
            self.apps_clear_button.clicked.connect(self.clear_apps)
            apps_header.addWidget(self.apps_label)
            apps_header.addStretch(1)
            apps_header.addWidget(self.apps_summary_label)
            apps_header.addWidget(self.apps_filter_input)
            apps_header.addWidget(self.load_apps_button)
            apps_header.addWidget(self.apps_select_all_button)
            apps_header.addWidget(self.apps_clear_button)
            apps_layout.addLayout(apps_header)
            self.apps_table = TableWidget()
            self.apps_table.setColumnCount(5)
            self.apps_table.setWordWrap(False)
            self.apps_table.verticalHeader().setVisible(False)
            self.apps_table.setMinimumHeight(230)
            self.apps_table.setMaximumHeight(330)
            self.apps_table.itemChanged.connect(self.on_app_item_changed)
            make_table_columns_resizable(self.apps_table, 48)
            self.apps_table.setColumnWidth(1, 260)
            self.apps_table.setColumnWidth(2, 120)
            self.apps_table.setColumnWidth(3, 180)
            self.apps_table.setColumnWidth(0, 48)
            apps_layout.addWidget(self.apps_table)
            self.trap_child_wheel(self.apps_table)

            _, action_layout = self.add_card()
            self.output_label = SubtitleLabel()
            action_layout.addWidget(self.output_label)
            control_row = QHBoxLayout()
            control_row.setSpacing(14)
            self.archive_format_label = BodyLabel()
            self.format_combo = ComboBox()
            self.format_combo.addItems(["zip", "7z", "directory"])
            self.encrypt_checkbox = CheckBox()
            self.encrypt_checkbox.setChecked(settings.encrypt_by_default)
            self.password_input = PasswordLineEdit()
            self.password_input.setMinimumWidth(260)
            backup_button = PrimaryPushButton(icon("SAVE"), "")
            backup_button.setMinimumHeight(46)
            backup_button.clicked.connect(self.create_backup)
            self.backup_button = backup_button
            self.stop_backup_button = PushButton(icon("CANCEL"), "")
            self.stop_backup_button.setMaximumWidth(120)
            self.stop_backup_button.setEnabled(False)
            self.stop_backup_button.clicked.connect(self.request_stop_smart_backup)
            control_row.addWidget(self.archive_format_label)
            control_row.addWidget(self.format_combo)
            control_row.addWidget(self.encrypt_checkbox)
            control_row.addWidget(self.password_input)
            control_row.addStretch(1)
            control_row.addWidget(backup_button)
            control_row.addWidget(self.stop_backup_button)
            action_layout.addLayout(control_row)
            self.smart_progress_bar = QProgressBar()
            self.smart_progress_bar.setRange(0, 100)
            self.smart_progress_bar.setValue(0)
            self.smart_log_text = TextEdit()
            self.smart_log_text.setReadOnly(True)
            self.smart_log_text.setMaximumHeight(130)
            action_layout.addWidget(self.smart_progress_bar)
            action_layout.addWidget(self.smart_log_text)
            self.root_layout.addStretch(1)
            self.retranslate()
            self.update_selection_label()

        def populate_table(self) -> None:
            self.loading_table = True
            self.table.setUpdatesEnabled(False)
            self.table.blockSignals(True)
            try:
                self.table.setRowCount(len(self.items))
                for row, item in enumerate(self.items):
                    check_item = QTableWidgetItem()
                    check_item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsSelectable
                    )
                    check_state = (
                        Qt.CheckState.Checked
                        if item.id in self.selected_ids
                        else Qt.CheckState.Unchecked
                    )
                    check_item.setCheckState(check_state)
                    self.table.setItem(row, 0, check_item)
                    self.item_by_row[row] = item
            finally:
                self.table.blockSignals(False)
                self.table.setUpdatesEnabled(True)
                self.loading_table = False
            self.refresh_table_text()

        def refresh_table_text(self) -> None:
            self.table.setHorizontalHeaderLabels(
                [
                    "",
                    t("name"),
                    t("category"),
                    t("state"),
                    t("path"),
                    t("reason"),
                ]
            )
            self.table.setUpdatesEnabled(False)
            for row, item in self.item_by_row.items():
                values = [
                    item.name,
                    item.category,
                    t("exists") if item.path.exists() else t("missing"),
                    str(item.path),
                    item.reason,
                ]
                for column, value in enumerate(values, start=1):
                    table_item = self.table.item(row, column)
                    if table_item is None:
                        table_item = QTableWidgetItem()
                        self.table.setItem(row, column, table_item)
                    table_item.setText(value)
                    table_item.setToolTip(value)
                    table_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setUpdatesEnabled(True)
            self.filter_backup_items()

        def filter_backup_items(self) -> None:
            if not hasattr(self, "table"):
                return
            query = self.item_filter_input.text().strip().casefold()
            for row, item in self.item_by_row.items():
                search_text = " ".join(
                    (item.name, item.category, item.software or "", str(item.path), item.reason)
                ).casefold()
                self.table.setRowHidden(row, bool(query and query not in search_text))

        def set_backup_items_checked(self, checked: bool) -> None:
            self.loading_table = True
            if checked and settings.sensitive_confirm:
                self.selected_ids = {item.id for item in self.items if not item.sensitive}
            else:
                self.selected_ids = {item.id for item in self.items} if checked else set()
            self.excluded_paths_by_item.clear()
            self.included_paths_by_item.clear()
            for row, item in self.item_by_row.items():
                check_item = self.table.item(row, 0)
                if check_item:
                    state = (
                        Qt.CheckState.Checked
                        if item.id in self.selected_ids
                        else Qt.CheckState.Unchecked
                    )
                    check_item.setCheckState(state)
            self.loading_table = False
            self.update_selection_label()

        def select_all_backup_items(self) -> None:
            self.set_backup_items_checked(True)

        def clear_backup_items(self) -> None:
            self.set_backup_items_checked(False)

        def update_main_item_check_state(self, item_id: str) -> None:
            row = next((row for row, item in self.item_by_row.items() if item.id == item_id), None)
            if row is None:
                return
            if item_id not in self.selected_ids:
                state = Qt.CheckState.Unchecked
            elif (
                self.excluded_paths_by_item.get(item_id)
                or self.included_paths_by_item.get(item_id)
            ):
                state = Qt.CheckState.PartiallyChecked
            else:
                state = Qt.CheckState.Checked
            check_item = self.table.item(row, 0)
            if not check_item:
                return
            self.loading_table = True
            check_item.setCheckState(state)
            self.loading_table = False

        def remove_exclusions_under_path(self, item_id: str, relative_path: str) -> None:
            exclusions = self.excluded_paths_by_item.get(item_id)
            if exclusions is None:
                return
            normalized = relative_path.strip("/")
            if not normalized:
                exclusions.clear()
            else:
                exclusions.difference_update(
                    {
                        excluded
                        for excluded in exclusions
                        if excluded == normalized or excluded.startswith(f"{normalized}/")
                    }
                )
            if not exclusions:
                self.excluded_paths_by_item.pop(item_id, None)

        def select_preview_path(self, item_id: str, relative_path: str) -> None:
            normalized = relative_path.strip("/")
            if not normalized:
                self.selected_ids.add(item_id)
                self.excluded_paths_by_item.pop(item_id, None)
                self.included_paths_by_item.pop(item_id, None)
                return

            inclusions = self.included_paths_by_item.get(item_id)
            if inclusions is None and item_id in self.selected_ids:
                self.remove_exclusions_under_path(item_id, normalized)
                return

            self.selected_ids.add(item_id)
            inclusions = self.included_paths_by_item.setdefault(item_id, set())
            has_selected_parent = any(
                normalized.startswith(f"{included}/") for included in inclusions
            )
            if not has_selected_parent:
                inclusions.difference_update(
                    {
                        included
                        for included in inclusions
                        if included == normalized or included.startswith(f"{normalized}/")
                    }
                )
                inclusions.add(normalized)
            self.remove_exclusions_under_path(item_id, normalized)

        def deselect_preview_path(self, item_id: str, relative_path: str) -> None:
            normalized = relative_path.strip("/")
            if not normalized:
                self.selected_ids.discard(item_id)
                self.excluded_paths_by_item.pop(item_id, None)
                self.included_paths_by_item.pop(item_id, None)
                return

            inclusions = self.included_paths_by_item.get(item_id)
            if inclusions is None:
                self.selected_ids.add(item_id)
                exclusions = self.excluded_paths_by_item.setdefault(item_id, set())
                exclusions.difference_update(
                    {
                        excluded
                        for excluded in exclusions
                        if excluded == normalized or excluded.startswith(f"{normalized}/")
                    }
                )
                exclusions.add(normalized)
                return

            has_selected_parent = any(
                normalized.startswith(f"{included}/") for included in inclusions
            )
            if has_selected_parent:
                exclusions = self.excluded_paths_by_item.setdefault(item_id, set())
                exclusions.difference_update(
                    {
                        excluded
                        for excluded in exclusions
                        if excluded == normalized or excluded.startswith(f"{normalized}/")
                    }
                )
                exclusions.add(normalized)
            else:
                inclusions.difference_update(
                    {
                        included
                        for included in inclusions
                        if included == normalized or included.startswith(f"{normalized}/")
                    }
                )
            if not inclusions:
                self.selected_ids.discard(item_id)
                self.included_paths_by_item.pop(item_id, None)
                self.excluded_paths_by_item.pop(item_id, None)

        def refresh_tree_ancestor_states(self, tree_item: QTreeWidgetItem) -> None:
            parent = tree_item.parent()
            self.loading_preview_tree = True
            while parent:
                states = [
                    parent.child(index).checkState(0)
                    for index in range(parent.childCount())
                    if not parent.child(index).data(0, DUMMY_NODE_ROLE)
                ]
                if states and all(state == Qt.CheckState.Checked for state in states):
                    parent.setCheckState(0, Qt.CheckState.Checked)
                elif states and all(state == Qt.CheckState.Unchecked for state in states):
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
                parent = parent.parent()
            self.loading_preview_tree = False

        def on_preview_tree_item_changed(
            self,
            tree_item: QTreeWidgetItem,
            column: int,
        ) -> None:
            if self.loading_preview_tree or column != 0:
                return
            item_id = tree_item.data(0, ITEM_ID_ROLE)
            relative_path = tree_item.data(0, RELATIVE_PATH_ROLE) or ""
            if not item_id:
                return
            state = tree_item.checkState(0)
            self.loading_preview_tree = True
            if state == Qt.CheckState.Checked:
                selected_item = next(
                    (candidate for candidate in self.items if candidate.id == item_id),
                    None,
                )
                if (
                    selected_item
                    and selected_item.sensitive
                    and settings.sensitive_confirm
                    and not self.confirm_sensitive_item(selected_item)
                ):
                    tree_item.setCheckState(Qt.CheckState.Unchecked)
                    self.loading_preview_tree = False
                    return
                self.select_preview_path(item_id, relative_path)
                self.set_tree_item_check_state(tree_item, Qt.CheckState.Checked)
            elif state == Qt.CheckState.Unchecked:
                self.deselect_preview_path(item_id, relative_path)
                self.set_tree_item_check_state(tree_item, Qt.CheckState.Unchecked)
            self.loading_preview_tree = False
            self.refresh_tree_ancestor_states(tree_item)
            self.update_main_item_check_state(item_id)
            self.update_selection_label()
            if self.pending_preview_row is not None:
                self.update_file_preview(self.pending_preview_row)

        def populate_apps_table(self) -> None:
            self.loading_apps = True
            self.app_by_row.clear()
            self.apps_table.setUpdatesEnabled(False)
            self.apps_table.blockSignals(True)
            try:
                self.apps_table.setRowCount(len(self.installed_apps))
                for row, app in enumerate(self.installed_apps):
                    check_item = QTableWidgetItem()
                    check_item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsSelectable
                    )
                    check_item.setCheckState(Qt.CheckState.Unchecked)
                    self.apps_table.setItem(row, 0, check_item)
                    self.app_by_row[row] = app
            finally:
                self.apps_table.blockSignals(False)
                self.apps_table.setUpdatesEnabled(True)
                self.loading_apps = False
            self.refresh_apps_text()

        def load_installed_apps(self, select_all_after_load: bool = False) -> None:
            if self.apps_thread and self.apps_thread.isRunning():
                self.select_all_apps_after_load = (
                    self.select_all_apps_after_load or select_all_after_load
                )
                return
            self.select_all_apps_after_load = select_all_after_load
            self.load_apps_button.setEnabled(False)
            self.apps_summary_label.setText(t("loading_apps"))
            self.apps_thread = QThread(self)
            self.apps_worker = InstalledAppsWorker()
            self.apps_worker.moveToThread(self.apps_thread)
            self.apps_thread.started.connect(self.apps_worker.run)
            self.apps_worker.finished.connect(self.on_apps_loaded)
            self.apps_worker.failed.connect(self.on_apps_failed)
            self.apps_worker.finished.connect(self.apps_thread.quit)
            self.apps_worker.failed.connect(self.apps_thread.quit)
            self.apps_worker.finished.connect(self.apps_worker.deleteLater)
            self.apps_worker.failed.connect(self.apps_worker.deleteLater)
            self.apps_thread.finished.connect(self.apps_thread.deleteLater)
            self.apps_thread.finished.connect(self.cleanup_apps_worker)
            self.apps_thread.start()

        def cleanup_apps_worker(self) -> None:
            self.apps_thread = None
            self.apps_worker = None

        def on_apps_loaded(self, apps: object) -> None:
            self.installed_apps = list(apps)
            self.selected_app_names = (
                {app.name for app in self.installed_apps}
                if self.select_all_apps_after_load
                else set()
            )
            self.populate_apps_table()
            if self.select_all_apps_after_load:
                self.set_apps_checked(True)
            self.select_all_apps_after_load = False
            self.load_apps_button.setEnabled(True)

        def on_apps_failed(self, message: str) -> None:
            self.select_all_apps_after_load = False
            self.load_apps_button.setEnabled(True)
            self.apps_summary_label.setText(message)

        def refresh_apps_text(self) -> None:
            self.apps_table.setHorizontalHeaderLabels(
                ["", t("name"), "Version", "Publisher", t("path")]
            )
            self.apps_table.setUpdatesEnabled(False)
            for row, app in self.app_by_row.items():
                values = [
                    app.name,
                    app.version or "",
                    app.publisher or "",
                    app.install_location or app.icon_path or "",
                ]
                for column, value in enumerate(values, start=1):
                    table_item = self.apps_table.item(row, column)
                    if table_item is None:
                        table_item = QTableWidgetItem()
                        self.apps_table.setItem(row, column, table_item)
                    table_item.setText(value)
                    table_item.setToolTip(value)
                    if column == 1:
                        icon_path = normalize_icon_path(app.icon_path)
                        if icon_path:
                            table_item.setIcon(self.icon_provider.icon(QFileInfo(str(icon_path))))
                    table_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.apps_table.setUpdatesEnabled(True)
            self.filter_apps()
            self.update_apps_summary()

        def filter_apps(self) -> None:
            if not hasattr(self, "apps_table"):
                return
            query = self.apps_filter_input.text().strip().casefold()
            for row, app in self.app_by_row.items():
                search_text = " ".join(
                    (
                        app.name,
                        app.version or "",
                        app.publisher or "",
                        app.install_location or "",
                    )
                ).casefold()
                self.apps_table.setRowHidden(row, bool(query and query not in search_text))

        def set_apps_checked(self, checked: bool) -> None:
            if checked and not self.installed_apps:
                self.load_installed_apps(select_all_after_load=True)
                return
            self.loading_apps = True
            self.selected_app_names = (
                {app.name for app in self.installed_apps} if checked else set()
            )
            state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            for row in self.app_by_row:
                check_item = self.apps_table.item(row, 0)
                if check_item:
                    check_item.setCheckState(state)
            self.loading_apps = False
            self.update_apps_summary()

        def select_all_apps(self) -> None:
            self.set_apps_checked(True)

        def clear_apps(self) -> None:
            self.set_apps_checked(False)

        def on_app_item_changed(self, table_item: QTableWidgetItem) -> None:
            if self.loading_apps or table_item.column() != 0:
                return
            app = self.app_by_row.get(table_item.row())
            if app is None:
                return
            if table_item.checkState() == Qt.CheckState.Checked:
                self.selected_app_names.add(app.name)
            else:
                self.selected_app_names.discard(app.name)
            self.update_apps_summary()

        def update_apps_summary(self) -> None:
            if hasattr(self, "apps_summary_label"):
                key = (
                    "installed_apps_summary"
                    if self.installed_apps
                    else "installed_apps_not_loaded"
                )
                self.apps_summary_label.setText(
                    t(key, count=len(self.installed_apps), selected=len(self.selected_app_names))
                )

        def on_item_changed(self, table_item: QTableWidgetItem) -> None:
            if self.loading_table or table_item.column() != 0:
                return
            item = self.item_by_row.get(table_item.row())
            if item is None:
                return
            check_state = table_item.checkState()
            if check_state == Qt.CheckState.Checked:
                needs_confirmation = item.sensitive and settings.sensitive_confirm
                if needs_confirmation and not self.confirm_sensitive_item(item):
                    self.loading_table = True
                    table_item.setCheckState(Qt.CheckState.Unchecked)
                    self.loading_table = False
                    return
                self.selected_ids.add(item.id)
                self.excluded_paths_by_item.pop(item.id, None)
                self.included_paths_by_item.pop(item.id, None)
            elif check_state == Qt.CheckState.Unchecked:
                self.selected_ids.discard(item.id)
                self.excluded_paths_by_item.pop(item.id, None)
                self.included_paths_by_item.pop(item.id, None)
            else:
                self.selected_ids.add(item.id)
            self.update_selection_label()
            if self.table.currentRow() == table_item.row():
                self.prepare_file_preview(table_item.row())

        def confirm_sensitive_item(self, item: BackupItem) -> bool:
            dialog = MessageBox(
                t("sensitive_title"),
                t("sensitive_body", name=item.name),
                self,
            )
            return bool(dialog.exec())

        def on_current_item_changed(self, row: int, *_: object) -> None:
            self.prepare_file_preview(row)

        def clear_file_preview(self) -> None:
            self.preview_paths = []
            self.file_preview_tree.clear()
            self.clear_preview_file_views()

        def clear_preview_file_views(self) -> None:
            self.file_preview_list.clear()
            self.file_preview_table.clearContents()
            self.file_preview_table.setRowCount(0)
            self.file_preview_icons.clear()

        def tree_check_state_for(self, item: BackupItem, relative_path: str) -> Qt.CheckState:
            if item.id not in self.selected_ids:
                return Qt.CheckState.Unchecked
            excluded = self.excluded_paths_by_item.get(item.id, set())
            included = self.included_paths_by_item.get(item.id)
            normalized = relative_path.strip("/")
            if included:
                is_included = any(
                    normalized == selected or normalized.startswith(f"{selected}/")
                    for selected in included
                )
                if is_included:
                    if is_excluded_relative(relative_path, excluded):
                        return Qt.CheckState.Unchecked
                    if has_excluded_child(relative_path, excluded):
                        return Qt.CheckState.PartiallyChecked
                    return Qt.CheckState.Checked
                if has_included_child(relative_path, included):
                    return Qt.CheckState.PartiallyChecked
                return Qt.CheckState.Unchecked
            if is_excluded_relative(relative_path, excluded):
                return Qt.CheckState.Unchecked
            if has_excluded_child(relative_path, excluded):
                return Qt.CheckState.PartiallyChecked
            return Qt.CheckState.Checked

        def set_tree_item_check_state(
            self,
            tree_item: QTreeWidgetItem,
            check_state: Qt.CheckState,
        ) -> None:
            tree_item.setCheckState(0, check_state)
            for index in range(tree_item.childCount()):
                child = tree_item.child(index)
                if not child.data(0, DUMMY_NODE_ROLE):
                    self.set_tree_item_check_state(child, check_state)

        def direct_child_entries(self, path: Path) -> list[Path]:
            if path.is_file() or not path.exists():
                return []
            try:
                children = []
                with os.scandir(path) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 240:
                            break
                        children.append(Path(entry.path))
            except OSError:
                return []
            return sorted(children, key=lambda child: (child.is_file(), child.name.lower()))

        def add_lazy_dummy_if_needed(self, tree_item: QTreeWidgetItem, path: Path) -> None:
            if path.is_dir() and tree_item.childCount() == 0:
                dummy = QTreeWidgetItem([""])
                dummy.setData(0, DUMMY_NODE_ROLE, True)
                tree_item.addChild(dummy)

        def make_preview_tree_item(
            self,
            item: BackupItem,
            path: Path,
            parent: QTreeWidgetItem | None,
        ) -> QTreeWidgetItem:
            try:
                relative = "" if path == item.path else path.relative_to(item.path).as_posix()
            except ValueError:
                relative = path.name
            label = str(path) if path == item.path else path.name
            tree_item = QTreeWidgetItem([label])
            tree_item.setIcon(0, self.icon_provider.icon(QFileInfo(str(path))))
            tree_item.setData(0, PATH_ROLE, str(path))
            tree_item.setData(0, ITEM_ID_ROLE, item.id)
            tree_item.setData(0, RELATIVE_PATH_ROLE, relative)
            flags = (
                tree_item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            tree_item.setFlags(flags)
            tree_item.setCheckState(0, self.tree_check_state_for(item, relative))
            if parent is None:
                self.file_preview_tree.addTopLevelItem(tree_item)
            else:
                parent.addChild(tree_item)
            self.add_lazy_dummy_if_needed(tree_item, path)
            return tree_item

        def populate_preview_tree_children(self, tree_item: QTreeWidgetItem) -> None:
            if tree_item.childCount() == 1 and tree_item.child(0).data(
                0,
                DUMMY_NODE_ROLE,
            ):
                tree_item.takeChildren()
            else:
                return
            item_id = tree_item.data(0, ITEM_ID_ROLE)
            item = next((candidate for candidate in self.items if candidate.id == item_id), None)
            path_text = tree_item.data(0, PATH_ROLE)
            if item is None or not path_text:
                return
            path = Path(path_text)
            self.loading_preview_tree = True
            self.file_preview_tree.setUpdatesEnabled(False)
            try:
                for child in self.direct_child_entries(path):
                    self.make_preview_tree_item(item, child, tree_item)
            finally:
                self.file_preview_tree.setUpdatesEnabled(True)
                self.loading_preview_tree = False

        def prepare_file_preview(self, row: int) -> None:
            self.pending_preview_row = row
            self.clear_file_preview()
            item = self.item_by_row.get(row)
            if item is None:
                return
            self.file_preview_tree.setHeaderLabels([t("path_tree")])
            self.loading_preview_tree = True
            self.make_preview_tree_item(item, item.path, None)
            self.loading_preview_tree = False
            self.file_preview_list.addItem(t("preview_click_to_load"))

        def load_current_file_preview(self) -> None:
            row = self.table.currentRow()
            if row >= 0:
                self.pending_preview_row = row
            self.load_pending_file_preview()

        def load_pending_file_preview(self) -> None:
            if self.pending_preview_row is None:
                return
            self.update_file_preview(self.pending_preview_row)

        def update_file_preview(self, row: int) -> None:
            self.clear_preview_file_views()
            item = self.item_by_row.get(row)
            if item is None:
                return
            self.file_preview_list.addItem(t("loading_preview"))
            if self.preview_thread and self.preview_thread.isRunning():
                self.queued_preview_row = row
                return
            self.preview_thread = QThread(self)
            self.preview_worker = PreviewFilesWorker(
                row,
                item,
                self.excluded_paths_by_item.get(item.id, set()),
                self.included_paths_by_item.get(item.id),
            )
            self.preview_worker.moveToThread(self.preview_thread)
            self.preview_thread.started.connect(self.preview_worker.run)
            self.preview_worker.finished.connect(self.on_preview_files_loaded)
            self.preview_worker.failed.connect(self.on_preview_files_failed)
            self.preview_worker.finished.connect(self.preview_thread.quit)
            self.preview_worker.failed.connect(self.preview_thread.quit)
            self.preview_worker.finished.connect(self.preview_worker.deleteLater)
            self.preview_worker.failed.connect(self.preview_worker.deleteLater)
            self.preview_thread.finished.connect(self.preview_thread.deleteLater)
            self.preview_thread.finished.connect(self.cleanup_preview_worker)
            self.preview_thread.start()

        def cleanup_preview_worker(self) -> None:
            self.preview_thread = None
            self.preview_worker = None
            queued = self.queued_preview_row
            self.queued_preview_row = None
            if queued is not None:
                self.pending_preview_row = queued
                self.update_file_preview(queued)

        def on_preview_files_failed(self, row: int, message: str) -> None:
            if row != self.pending_preview_row:
                return
            self.clear_preview_file_views()
            self.file_preview_list.addItem(message)

        def on_preview_files_loaded(self, row: int, files: object) -> None:
            if row != self.pending_preview_row:
                return
            item = self.item_by_row.get(row)
            if item is None:
                return
            files = list(files)
            self.file_preview_list.clear()
            self.preview_paths = [file.path for file in files]
            self.file_preview_table.setHorizontalHeaderLabels(
                [t("name"), "Size", t("folder_path"), t("path")]
            )
            self.file_preview_list.setUpdatesEnabled(False)
            self.file_preview_icons.setUpdatesEnabled(False)
            self.file_preview_table.setUpdatesEnabled(False)
            self.file_preview_table.setRowCount(len(files))
            try:
                for index, file in enumerate(files):
                    icon_value = self.icon_provider.icon(QFileInfo(str(file.path)))
                    text = f"{file.path.name} ({file.size} bytes)"
                    list_item = QListWidgetItem(icon_value, text)
                    list_item.setData(PATH_ROLE, str(file.path))
                    self.file_preview_list.addItem(list_item)

                    icon_item = QListWidgetItem(icon_value, file.path.name)
                    icon_item.setToolTip(str(file.path))
                    icon_item.setData(PATH_ROLE, str(file.path))
                    self.file_preview_icons.addItem(icon_item)

                    try:
                        relative = file.path.relative_to(item.path)
                    except ValueError:
                        relative = Path(file.path.name)
                    parent_parts = relative.parts[:-1]
                    values = [
                        file.path.name,
                        str(file.size),
                        str(Path(*parent_parts)) if parent_parts else ".",
                        str(file.path),
                    ]
                    for column, value in enumerate(values):
                        table_item = QTableWidgetItem(value)
                        table_item.setData(PATH_ROLE, str(file.path))
                        table_item.setToolTip(str(file.path) if column == 3 else value)
                        table_item.setFlags(
                            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                        )
                        if column == 0:
                            table_item.setIcon(icon_value)
                        self.file_preview_table.setItem(index, column, table_item)
            finally:
                self.file_preview_table.setUpdatesEnabled(True)
                self.file_preview_icons.setUpdatesEnabled(True)
                self.file_preview_list.setUpdatesEnabled(True)
                make_table_columns_resizable(self.file_preview_table)
                self.file_preview_table.setColumnWidth(0, 220)
                self.file_preview_table.setColumnWidth(1, 90)
                self.file_preview_table.setColumnWidth(2, 220)

        def on_preview_tree_item_expanded(self, tree_item: QTreeWidgetItem) -> None:
            self.populate_preview_tree_children(tree_item)

        def change_file_preview_mode(self) -> None:
            self.file_preview_stack.setCurrentIndex(self.file_view_combo.currentIndex())

        def open_preview_item(self, item: QListWidgetItem) -> None:
            path = item.data(PATH_ROLE)
            if path:
                open_local_path(Path(path), "Opening preview file with system default")

        def open_preview_table_item(self, item: QTableWidgetItem) -> None:
            path = item.data(PATH_ROLE)
            if path:
                open_local_path(Path(path), "Opening preview file with system default")

        def open_preview_tree_item(self, item: QTreeWidgetItem) -> None:
            path = item.data(0, PATH_ROLE)
            if path:
                open_local_path(Path(path), "Opening preview file with system default")

        def show_preview_context_menu(self, view, position) -> None:
            entry = view.itemAt(position)
            path = entry.data(0, PATH_ROLE) if view is self.file_preview_tree and entry else None
            if entry and view is not self.file_preview_tree:
                path = entry.data(PATH_ROLE)
            if not path:
                return
            menu = RoundMenu(parent=view)
            menu.setItemHeight(36)
            explorer_action = Action(
                icon("FOLDER", "VIEW"),
                t("open_in_file_explorer"),
                triggered=lambda: self.open_in_file_explorer(str(path)),
            )
            menu.addAction(explorer_action)
            menu.exec(view.viewport().mapToGlobal(position))

        def open_in_file_explorer(self, path_text: str) -> None:
            path = Path(path_text)
            directory = path if path.is_dir() else path.parent
            try:
                if os.name == "nt":
                    arguments = (
                        ["explorer.exe", f"/select,{path}"]
                        if path.is_file()
                        else ["explorer.exe", str(directory)]
                    )
                    subprocess.Popen(
                        arguments,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    open_local_path(directory, "Opening file explorer")
                logger.info("Opened in file explorer: %s", path)
            except OSError:
                open_local_path(directory, "Opening file explorer")

        def update_selection_label(self) -> None:
            total = len(self.items)
            selected = len(self.selected_ids)
            missing = sum(
                1
                for item in self.items
                if item.id in self.selected_ids and not item.path.exists()
            )
            self.backup_items_label.setText(
                t("backup_items_selected", selected=selected, total=total, missing=missing)
            )

        def create_backup(self) -> None:
            if (
                (self.backup_thread and self.backup_thread.isRunning())
                or (self.preflight_thread and self.preflight_thread.isRunning())
            ):
                return
            password = password_or_none(self.encrypt_checkbox, self.password_input)
            if password == "":
                MessageBox(t("missing_password"), t("missing_password_body"), self).exec()
                return
            archive_format = ArchiveFormat(self.format_combo.currentText())
            if archive_format == ArchiveFormat.DIRECTORY:
                directory = QFileDialog.getExistingDirectory(self, t("choose_backup_destination"))
                if not directory:
                    return
                output_path = Path(directory) / default_package_name(
                    "backUpHelper-smart",
                    archive_format,
                )
            else:
                result = save_package_path(
                    self,
                    t("choose_backup_destination"),
                    default_package_name("backUpHelper-smart", archive_format),
                    (ArchiveFormat.ZIP, ArchiveFormat.SEVEN_Z),
                )
                if not result:
                    return
                output_path, archive_format = result
                self.format_combo.setCurrentText(archive_format.value)
            if not output_path:
                return
            selected = [item for item in self.items if item.id in self.selected_ids]
            selected_app_names = set(self.selected_app_names)
            if not selected and not selected_app_names:
                MessageBox(t("nothing_selected"), t("nothing_selected_body"), self).exec()
                return
            item_exclusions = {
                item_id: set(paths)
                for item_id, paths in self.excluded_paths_by_item.items()
                if paths
            }
            item_inclusions = {
                item_id: set(paths)
                for item_id, paths in self.included_paths_by_item.items()
                if paths
            }
            temporary_root = configured_temporary_root()
            self.smart_log_text.clear()
            self.log_smart(t("backup_starting"))
            for item in selected:
                self.log_smart(f"Selected item: {item.name} -> {item.path}")
            for app_name in sorted(selected_app_names):
                self.log_smart(f"Selected app inventory: {app_name}")

            def build_task(space_estimate):
                def task(progress, cancel_event):
                    return create_backup_package(
                        output_path.parent,
                        selected,
                        archive_format,
                        include_system_inventory=bool(selected_app_names),
                        encryption_password=password,
                        selected_application_names=selected_app_names,
                        item_exclusions=item_exclusions,
                        item_inclusions=item_inclusions,
                        output_path=output_path,
                        progress=progress,
                        temporary_root=temporary_root,
                        space_estimate=space_estimate,
                        cancel_event=cancel_event,
                    )

                return task

            self.start_smart_preflight_worker(
                output_path,
                temporary_root,
                selected,
                archive_format,
                bool(password),
                item_exclusions,
                item_inclusions,
                build_task,
            )

        def start_smart_preflight_worker(
            self,
            output_path: Path,
            temporary_root: Path | None,
            selected: list[BackupItem],
            archive_format: ArchiveFormat,
            encryption_enabled: bool,
            item_exclusions: dict[str, set[str]],
            item_inclusions: dict[str, set[str]],
            build_task,
        ) -> None:
            self.backup_button.setEnabled(False)
            self.stop_backup_button.setEnabled(True)
            self.smart_progress_bar.setRange(0, 0)
            self.log_smart(t("space_checking"))
            self.preflight_build_task = build_task

            def task(_progress, cancel_event):
                estimate = estimate_backup_space(
                    selected,
                    archive_format,
                    encryption_enabled,
                    item_exclusions,
                    item_inclusions,
                    cancel_event,
                )
                return check_backup_space(output_path, temporary_root, estimate)

            self.preflight_thread = QThread(self)
            self.preflight_worker = BackupJobWorker(task)
            self.preflight_worker.moveToThread(self.preflight_thread)
            self.preflight_thread.started.connect(self.preflight_worker.run)
            self.preflight_worker.finished.connect(self.on_smart_preflight_finished)
            self.preflight_worker.failed.connect(self.on_smart_preflight_failed)
            self.preflight_worker.cancelled.connect(self.on_smart_backup_cancelled)
            self.preflight_worker.finished.connect(self.preflight_thread.quit)
            self.preflight_worker.failed.connect(self.preflight_thread.quit)
            self.preflight_worker.cancelled.connect(self.preflight_thread.quit)
            self.preflight_worker.finished.connect(self.preflight_worker.deleteLater)
            self.preflight_worker.failed.connect(self.preflight_worker.deleteLater)
            self.preflight_worker.cancelled.connect(self.preflight_worker.deleteLater)
            self.preflight_thread.finished.connect(self.preflight_thread.deleteLater)
            self.preflight_thread.finished.connect(self.cleanup_smart_preflight_worker)
            self.preflight_thread.start()

        def cleanup_smart_preflight_worker(self) -> None:
            self.preflight_thread = None
            self.preflight_worker = None
            if not self.backup_thread or not self.backup_thread.isRunning():
                self.stop_backup_button.setEnabled(False)

        def on_smart_preflight_finished(self, check: BackupSpaceCheck) -> None:
            self.smart_progress_bar.setRange(0, 100)
            self.smart_progress_bar.setValue(0)
            body = t(
                "backup_space_body",
                source_size=format_bytes(check.estimate.source_bytes),
                file_count=check.estimate.source_files,
                output_required=format_bytes(check.destination_required_bytes),
                output_free=format_bytes(check.destination_free_bytes),
                temporary_required=format_bytes(check.temporary_required_bytes),
                temporary_free=format_bytes(check.temporary_free_bytes),
            )
            if not check.is_sufficient:
                self.log_smart(f"Backup space check failed: {body}")
                MessageBox(t("insufficient_space"), body, self).exec()
                self.preflight_build_task = None
                self.backup_button.setEnabled(True)
                return

            self.log_smart(f"Backup space check passed: {body}")
            dialog = MessageBox(t("backup_space_title"), body, self)
            dialog.yesButton.setText(t("continue_backup"))
            dialog.cancelButton.setText(t("cancel"))
            if not dialog.exec():
                self.preflight_build_task = None
                self.backup_button.setEnabled(True)
                return
            build_task = self.preflight_build_task
            self.preflight_build_task = None
            if build_task:
                self.start_smart_backup_worker(build_task(check.estimate))
            else:
                self.backup_button.setEnabled(True)

        def on_smart_preflight_failed(self, message: str) -> None:
            self.smart_progress_bar.setRange(0, 100)
            self.smart_progress_bar.setValue(0)
            self.log_smart(f"Backup space check failed: {message}")
            MessageBox(t("backup_failed"), message, self).exec()
            self.preflight_build_task = None
            self.backup_button.setEnabled(True)

        def start_smart_backup_worker(self, task) -> None:
            self.backup_button.setEnabled(False)
            self.stop_backup_button.setEnabled(True)
            self.smart_progress_bar.setRange(0, 100)
            self.smart_progress_bar.setValue(0)
            self.backup_thread = QThread(self)
            self.backup_worker = BackupJobWorker(task)
            self.backup_worker.moveToThread(self.backup_thread)
            self.backup_thread.started.connect(self.backup_worker.run)
            self.backup_worker.progress.connect(self.on_smart_backup_progress)
            self.backup_worker.finished.connect(self.on_smart_backup_finished)
            self.backup_worker.failed.connect(self.on_smart_backup_failed)
            self.backup_worker.cancelled.connect(self.on_smart_backup_cancelled)
            self.backup_worker.finished.connect(self.backup_thread.quit)
            self.backup_worker.failed.connect(self.backup_thread.quit)
            self.backup_worker.cancelled.connect(self.backup_thread.quit)
            self.backup_worker.finished.connect(self.backup_worker.deleteLater)
            self.backup_worker.failed.connect(self.backup_worker.deleteLater)
            self.backup_worker.cancelled.connect(self.backup_worker.deleteLater)
            self.backup_thread.finished.connect(self.backup_thread.deleteLater)
            self.backup_thread.finished.connect(self.cleanup_smart_backup_worker)
            self.backup_thread.start()

        def cleanup_smart_backup_worker(self) -> None:
            self.backup_thread = None
            self.backup_worker = None
            self.backup_button.setEnabled(True)
            self.stop_backup_button.setEnabled(False)

        def log_smart(self, message: str) -> None:
            logger.info(message)
            self.smart_log_text.append(message)

        def on_smart_backup_progress(self, message: str, current: int, total: int) -> None:
            if total <= 0 or current < 0:
                self.smart_progress_bar.setRange(0, 0)
            elif total > 0:
                if (
                    self.smart_progress_bar.minimum() != 0
                    or self.smart_progress_bar.maximum() != 100
                ):
                    self.smart_progress_bar.setRange(0, 100)
                self.smart_progress_bar.setValue(max(0, min(100, int(current / total * 100))))
            self.log_smart(message)

        def on_smart_backup_finished(self, package: object) -> None:
            self.smart_progress_bar.setValue(100)
            self.log_smart(f"Backup created: {package}")
            show_backup_created(self, package)

        def on_smart_backup_failed(self, message: str) -> None:
            self.log_smart(f"Backup failed: {message}")
            MessageBox(t("backup_failed"), message, self).exec()

        def on_smart_backup_cancelled(self, message: str) -> None:
            self.smart_progress_bar.setRange(0, 100)
            self.smart_progress_bar.setValue(0)
            self.log_smart(message)
            if not self.backup_thread or not self.backup_thread.isRunning():
                self.preflight_build_task = None
                self.backup_button.setEnabled(True)
            MessageBox(t("backup_cancelled"), t("backup_cancelled_body"), self).exec()

        def request_stop_smart_backup(self) -> None:
            worker = self.backup_worker or self.preflight_worker
            if not worker or not confirm_stop_operation(self):
                return
            worker.cancel()
            self.stop_backup_button.setEnabled(False)
            self.log_smart(t("stop_operation"))

        def retranslate(self) -> None:
            super().retranslate()
            if hasattr(self, "backup_items_label"):
                self.backup_items_label.setText(t("backup_items"))
                self.item_filter_input.setPlaceholderText(t("filter_backup_items"))
                self.backup_select_all_button.setText(t("select_all"))
                self.backup_clear_button.setText(t("clear_selection"))
                self.selected_files_label.setText(t("selected_item_files"))
                self.output_label.setText(t("output"))
                self.archive_format_label.setText(t("archive_format"))
                self.encrypt_checkbox.setText(t("encrypt_output"))
                self.password_input.setPlaceholderText(t("password"))
                self.backup_button.setText(t("create_smart_backup"))
                self.stop_backup_button.setText(t("stop"))
                self.file_view_label.setText(t("file_view"))
                self.file_view_combo.setItemText(0, t("view_tree"))
                self.file_view_combo.setItemText(1, t("view_list"))
                self.file_view_combo.setItemText(2, t("view_table"))
                self.file_view_combo.setItemText(3, t("view_icons"))
                self.preview_button.setText(t("load_preview"))
                self.apps_label.setText(t("installed_apps"))
                self.apps_filter_input.setPlaceholderText(t("filter_apps"))
                self.load_apps_button.setText(t("load_apps"))
                self.apps_select_all_button.setText(t("select_all"))
                self.apps_clear_button.setText(t("clear_selection"))
                self.refresh_apps_text()
                self.refresh_table_text()
                self.update_selection_label()

        def shutdown_workers(self) -> None:
            for thread in (self.preview_thread, self.apps_thread, self.preflight_thread):
                if thread and thread.isRunning():
                    thread.quit()
                    thread.wait(1500)

    class FullBackupPage(Page):
        def __init__(self) -> None:
            super().__init__("full_title", "full_subtitle")
            self.volume_destination_path: Path | None = None
            self.folder_path: Path | None = None
            self.folder_destination_path: Path | None = None
            self.full_backup_thread: QThread | None = None
            self.full_backup_worker: BackupJobWorker | None = None

            _, volume_layout = self.add_card()
            self.volume_title = SubtitleLabel()
            self.volume_hint = CaptionLabel()
            self.volume_hint.setWordWrap(True)
            volume_layout.addWidget(self.volume_title)
            volume_layout.addWidget(self.volume_hint)
            volume_grid = QGridLayout()
            volume_grid.setHorizontalSpacing(14)
            volume_grid.setVerticalSpacing(14)
            self.volume_label = BodyLabel()
            self.volume_combo = ComboBox()
            for volume in list_windows_volumes():
                self.volume_combo.addItem(str(volume))
            self.volume_destination_label = BodyLabel()
            self.volume_destination_input = LineEdit()
            self.volume_destination_input.setReadOnly(True)
            self.volume_destination_input.setMinimumHeight(40)
            self.volume_destination_button = PushButton(icon("SAVE"), "")
            self.volume_destination_button.setMaximumWidth(240)
            self.volume_destination_button.clicked.connect(self.choose_volume_destination)
            self.volume_encrypt_checkbox = CheckBox()
            self.volume_encrypt_checkbox.setChecked(settings.encrypt_by_default)
            self.volume_password_input = PasswordLineEdit()
            self.volume_password_input.setMinimumWidth(280)
            self.volume_button = PrimaryPushButton(icon("DISC", "SYNC"), "")
            self.volume_button.setMinimumHeight(46)
            self.volume_button.setMaximumWidth(280)
            self.volume_button.clicked.connect(self.create_volume_iso)
            volume_grid.addWidget(self.volume_label, 0, 0)
            volume_grid.addWidget(self.volume_combo, 0, 1)
            volume_grid.addWidget(self.volume_destination_label, 1, 0)
            volume_grid.addWidget(self.volume_destination_input, 1, 1)
            volume_grid.addWidget(self.volume_destination_button, 1, 2)
            volume_grid.addWidget(self.volume_encrypt_checkbox, 2, 1)
            volume_grid.addWidget(self.volume_password_input, 2, 2)
            volume_grid.addWidget(
                self.volume_button,
                3,
                2,
                alignment=Qt.AlignmentFlag.AlignRight,
            )
            volume_layout.addLayout(volume_grid)

            _, folder_layout = self.add_card()
            self.folder_title = SubtitleLabel()
            self.folder_hint = CaptionLabel()
            self.folder_hint.setWordWrap(True)
            folder_layout.addWidget(self.folder_title)
            folder_layout.addWidget(self.folder_hint)
            folder_grid = QGridLayout()
            folder_grid.setHorizontalSpacing(14)
            folder_grid.setVerticalSpacing(14)
            self.folder_label = BodyLabel()
            self.folder_input = LineEdit()
            self.folder_input.setReadOnly(True)
            self.folder_input.setMinimumHeight(40)
            self.folder_button = PushButton(icon("FOLDER"), "")
            self.folder_button.setMaximumWidth(240)
            self.folder_button.clicked.connect(self.choose_folder)
            self.folder_destination_label = BodyLabel()
            self.folder_destination_input = LineEdit()
            self.folder_destination_input.setReadOnly(True)
            self.folder_destination_input.setMinimumHeight(40)
            self.folder_destination_button = PushButton(icon("SAVE"), "")
            self.folder_destination_button.setMaximumWidth(240)
            self.folder_destination_button.clicked.connect(self.choose_folder_destination)
            self.folder_format_label = BodyLabel()
            self.folder_format_combo = ComboBox()
            self.folder_format_combo.addItems(["zip", "7z", "iso"])
            self.folder_encrypt_checkbox = CheckBox()
            self.folder_encrypt_checkbox.setChecked(settings.encrypt_by_default)
            self.folder_password_input = PasswordLineEdit()
            self.folder_password_input.setMinimumWidth(280)
            self.folder_create_button = PrimaryPushButton(icon("SYNC"), "")
            self.folder_create_button.setMinimumHeight(46)
            self.folder_create_button.setMaximumWidth(280)
            self.folder_create_button.clicked.connect(self.create_folder_backup)
            folder_grid.addWidget(self.folder_label, 0, 0)
            folder_grid.addWidget(self.folder_input, 0, 1)
            folder_grid.addWidget(self.folder_button, 0, 2)
            folder_grid.addWidget(self.folder_destination_label, 1, 0)
            folder_grid.addWidget(self.folder_destination_input, 1, 1)
            folder_grid.addWidget(self.folder_destination_button, 1, 2)
            folder_grid.addWidget(self.folder_format_label, 2, 0)
            folder_grid.addWidget(self.folder_format_combo, 2, 1)
            folder_grid.addWidget(self.folder_encrypt_checkbox, 3, 1)
            folder_grid.addWidget(self.folder_password_input, 3, 2)
            folder_grid.addWidget(
                self.folder_create_button,
                4,
                2,
                alignment=Qt.AlignmentFlag.AlignRight,
            )
            folder_layout.addLayout(folder_grid)

            _, progress_layout = self.add_card()
            self.full_progress_title = SubtitleLabel()
            self.full_stop_button = PushButton(icon("CANCEL"), "")
            self.full_stop_button.setMaximumWidth(120)
            self.full_stop_button.setEnabled(False)
            self.full_stop_button.clicked.connect(self.request_stop_full_backup)
            full_progress_header = QHBoxLayout()
            full_progress_header.addWidget(self.full_progress_title)
            full_progress_header.addStretch(1)
            full_progress_header.addWidget(self.full_stop_button)
            self.full_progress_bar = QProgressBar()
            self.full_progress_bar.setRange(0, 100)
            self.full_progress_bar.setValue(0)
            self.full_log_text = TextEdit()
            self.full_log_text.setReadOnly(True)
            self.full_log_text.setMaximumHeight(150)
            progress_layout.addLayout(full_progress_header)
            progress_layout.addWidget(self.full_progress_bar)
            progress_layout.addWidget(self.full_log_text)
            self.root_layout.addStretch(1)
            self.retranslate()

        def choose_volume_destination(self) -> None:
            result = save_package_path(
                self,
                t("choose_destination"),
                default_package_name("backUpHelper-volume", ArchiveFormat.ISO),
                (ArchiveFormat.ISO,),
            )
            if result:
                self.volume_destination_path = result[0]
                self.volume_destination_input.setText(str(result[0]))

        def create_volume_iso(self) -> None:
            if self.full_backup_thread and self.full_backup_thread.isRunning():
                return
            password = password_or_none(self.volume_encrypt_checkbox, self.volume_password_input)
            if password == "":
                MessageBox(t("missing_password"), t("missing_password_body"), self).exec()
                return
            if not self.volume_combo.currentText() or not self.volume_destination_path:
                MessageBox(t("missing_path"), t("missing_volume_body"), self).exec()
                return
            volume = Path(self.volume_combo.currentText())
            output_path = self.volume_destination_path
            temporary_root = configured_temporary_root()
            self.full_log_text.clear()
            self.log_full(t("backup_starting"))
            self.log_full(f"Selected volume: {volume}")
            self.log_full(f"Output package: {output_path}")

            def task(progress, cancel_event):
                return create_volume_iso_backup(
                    volume,
                    output_path.parent,
                    password,
                    output_path=output_path,
                    progress=progress,
                    temporary_root=temporary_root,
                    cancel_event=cancel_event,
                )

            self.start_full_backup_worker(task)

        def choose_folder(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, t("choose_folder"))
            if directory:
                self.folder_path = Path(directory)
                self.folder_input.setText(directory)

        def choose_folder_destination(self) -> None:
            archive_format = ArchiveFormat(self.folder_format_combo.currentText())
            result = save_package_path(
                self,
                t("choose_destination"),
                default_package_name("backUpHelper-folder", archive_format),
                (ArchiveFormat.ZIP, ArchiveFormat.SEVEN_Z, ArchiveFormat.ISO),
            )
            if result:
                self.folder_destination_path, selected_format = result
                self.folder_destination_input.setText(str(self.folder_destination_path))
                self.folder_format_combo.setCurrentText(selected_format.value)

        def create_folder_backup(self) -> None:
            if self.full_backup_thread and self.full_backup_thread.isRunning():
                return
            password = password_or_none(self.folder_encrypt_checkbox, self.folder_password_input)
            if password == "":
                MessageBox(t("missing_password"), t("missing_password_body"), self).exec()
                return
            if not self.folder_path or not self.folder_destination_path:
                MessageBox(t("missing_path"), t("missing_folder_body"), self).exec()
                return
            folder_path = self.folder_path
            output_path = self.folder_destination_path
            archive_format = ArchiveFormat(self.folder_format_combo.currentText())
            temporary_root = configured_temporary_root()
            self.full_log_text.clear()
            self.log_full(t("backup_starting"))
            self.log_full(f"Selected folder: {folder_path}")
            self.log_full(f"Output package: {output_path}")

            def task(progress, cancel_event):
                return create_folder_backup(
                    FullBackupTarget(path=folder_path, label=str(folder_path)),
                    output_path.parent,
                    archive_format,
                    password,
                    output_path=output_path,
                    progress=progress,
                    temporary_root=temporary_root,
                    cancel_event=cancel_event,
                )

            self.start_full_backup_worker(task)

        def start_full_backup_worker(self, task) -> None:
            self.volume_button.setEnabled(False)
            self.folder_create_button.setEnabled(False)
            self.full_stop_button.setEnabled(True)
            self.full_progress_bar.setRange(0, 100)
            self.full_progress_bar.setValue(0)
            self.full_backup_thread = QThread(self)
            self.full_backup_worker = BackupJobWorker(task)
            self.full_backup_worker.moveToThread(self.full_backup_thread)
            self.full_backup_thread.started.connect(self.full_backup_worker.run)
            self.full_backup_worker.progress.connect(self.on_full_backup_progress)
            self.full_backup_worker.finished.connect(self.on_full_backup_finished)
            self.full_backup_worker.failed.connect(self.on_full_backup_failed)
            self.full_backup_worker.cancelled.connect(self.on_full_backup_cancelled)
            self.full_backup_worker.finished.connect(self.full_backup_thread.quit)
            self.full_backup_worker.failed.connect(self.full_backup_thread.quit)
            self.full_backup_worker.cancelled.connect(self.full_backup_thread.quit)
            self.full_backup_worker.finished.connect(self.full_backup_worker.deleteLater)
            self.full_backup_worker.failed.connect(self.full_backup_worker.deleteLater)
            self.full_backup_worker.cancelled.connect(self.full_backup_worker.deleteLater)
            self.full_backup_thread.finished.connect(self.full_backup_thread.deleteLater)
            self.full_backup_thread.finished.connect(self.cleanup_full_backup_worker)
            self.full_backup_thread.start()

        def cleanup_full_backup_worker(self) -> None:
            self.full_backup_thread = None
            self.full_backup_worker = None
            self.volume_button.setEnabled(True)
            self.folder_create_button.setEnabled(True)
            self.full_stop_button.setEnabled(False)

        def log_full(self, message: str) -> None:
            logger.info(message)
            self.full_log_text.append(message)

        def on_full_backup_progress(self, message: str, current: int, total: int) -> None:
            if total <= 0 or current < 0:
                self.full_progress_bar.setRange(0, 0)
            elif total > 0:
                if self.full_progress_bar.minimum() != 0 or self.full_progress_bar.maximum() != 100:
                    self.full_progress_bar.setRange(0, 100)
                self.full_progress_bar.setValue(max(0, min(100, int(current / total * 100))))
            self.log_full(message)

        def on_full_backup_finished(self, package: object) -> None:
            self.full_progress_bar.setValue(100)
            self.log_full(f"Backup created: {package}")
            show_backup_created(self, package)

        def on_full_backup_failed(self, message: str) -> None:
            self.log_full(f"Backup failed: {message}")
            MessageBox(t("backup_failed"), message, self).exec()

        def on_full_backup_cancelled(self, message: str) -> None:
            self.full_progress_bar.setRange(0, 100)
            self.full_progress_bar.setValue(0)
            self.log_full(message)
            MessageBox(t("backup_cancelled"), t("backup_cancelled_body"), self).exec()

        def request_stop_full_backup(self) -> None:
            if not self.full_backup_worker or not confirm_stop_operation(self):
                return
            self.full_backup_worker.cancel()
            self.full_stop_button.setEnabled(False)
            self.log_full(t("stop_operation"))

        def shutdown_workers(self) -> None:
            if self.full_backup_thread and self.full_backup_thread.isRunning():
                logger.info("Waiting for active full backup worker before closing")
                self.full_backup_thread.quit()
                self.full_backup_thread.wait(1500)

        def retranslate(self) -> None:
            super().retranslate()
            if hasattr(self, "volume_title"):
                self.volume_title.setText(t("volume_iso"))
                self.volume_hint.setText(t("volume_iso_hint"))
                self.volume_label.setText(t("volume"))
                self.volume_destination_label.setText(t("destination"))
                self.volume_destination_button.setText(t("choose_destination"))
                self.volume_encrypt_checkbox.setText(t("encrypt_output"))
                self.volume_password_input.setPlaceholderText(t("password"))
                self.volume_button.setText(t("create_volume_iso"))
                self.folder_title.setText(t("folder_archive"))
                self.folder_hint.setText(t("folder_archive_hint"))
                self.folder_label.setText(t("folder"))
                self.folder_button.setText(t("choose_folder"))
                self.folder_destination_label.setText(t("destination"))
                self.folder_destination_button.setText(t("choose_destination"))
                self.folder_format_label.setText(t("archive_format"))
                self.folder_encrypt_checkbox.setText(t("encrypt_output"))
                self.folder_password_input.setPlaceholderText(t("password"))
                self.folder_create_button.setText(t("create_folder_backup"))
                self.full_progress_title.setText(t("backup_progress"))
                self.full_stop_button.setText(t("stop"))

    class PackageBrowserPage(Page):
        def __init__(self) -> None:
            super().__init__("browser_title", "browser_subtitle")
            self.current_package_path: Path | None = None
            self.current_entries = []
            self.entry_groups: dict[str, list] = {"data": [], "inventory": [], "recovery": []}
            self.data_entries_by_item: dict[str, list] = {}
            self.data_item_by_row: dict[int, str] = {}
            _, picker_layout = self.add_card()
            picker_row = QHBoxLayout()
            picker_row.setSpacing(12)
            self.package_input = LineEdit()
            self.package_input.setReadOnly(True)
            self.package_input.setMinimumHeight(40)
            self.open_file_button = PushButton(icon("FOLDER"), "")
            self.open_file_button.setMaximumWidth(210)
            self.open_file_button.clicked.connect(self.open_package_file)
            self.open_dir_button = PushButton(icon("FOLDER"), "")
            self.open_dir_button.setMaximumWidth(210)
            self.open_dir_button.clicked.connect(self.open_package_directory)
            self.extract_entry_button = PushButton(icon("SAVE", "FOLDER"), "")
            self.extract_entry_button.setMaximumWidth(190)
            self.extract_entry_button.setEnabled(False)
            self.extract_entry_button.clicked.connect(self.extract_selected_entry)
            self.extract_package_button = PushButton(icon("FOLDER", "SAVE"), "")
            self.extract_package_button.setMaximumWidth(190)
            self.extract_package_button.setEnabled(False)
            self.extract_package_button.clicked.connect(self.extract_package)
            picker_row.addWidget(self.package_input, 1)
            picker_row.addWidget(self.open_file_button)
            picker_row.addWidget(self.open_dir_button)
            picker_row.addWidget(self.extract_entry_button)
            picker_row.addWidget(self.extract_package_button)
            picker_layout.addLayout(picker_row)

            _, overview_layout = self.add_card()
            self.manifest_label = SubtitleLabel()
            overview_layout.addWidget(self.manifest_label)
            overview_grid = QGridLayout()
            overview_grid.setHorizontalSpacing(28)
            overview_grid.setVerticalSpacing(12)
            self.overview_fields: dict[str, CaptionLabel] = {}
            for index, key in enumerate(
                ["created_at", "mode", "format", "item_count", "file_count", "package_size"]
            ):
                label = BodyLabel()
                value = CaptionLabel("-")
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                overview_grid.addWidget(label, index // 3 * 2, index % 3)
                overview_grid.addWidget(value, index // 3 * 2 + 1, index % 3)
                self.overview_fields[key] = value
                setattr(self, f"overview_{key}_label", label)
            overview_layout.addLayout(overview_grid)

            _, workspace_layout = self.add_card()
            workspace_header = QHBoxLayout()
            self.browser_section_label = SubtitleLabel()
            self.browser_section_combo = ComboBox()
            self.browser_section_combo.addItems(["data", "applications", "conda", "recovery"])
            self.browser_section_combo.currentIndexChanged.connect(self.change_browser_section)
            workspace_header.addWidget(self.browser_section_label)
            workspace_header.addStretch(1)
            workspace_header.addWidget(self.browser_section_combo)
            workspace_layout.addLayout(workspace_header)

            self.browser_stack = QStackedWidget()
            self.data_workspace = QWidget()
            data_items_layout = QVBoxLayout(self.data_workspace)
            data_items_layout.setContentsMargins(0, 0, 0, 0)
            data_items_header = QHBoxLayout()
            self.data_items_label = SubtitleLabel()
            self.data_items_summary = CaptionLabel()
            data_items_header.addWidget(self.data_items_label)
            data_items_header.addStretch(1)
            data_items_header.addWidget(self.data_items_summary)
            data_items_layout.addLayout(data_items_header)
            self.data_items_table = TableWidget()
            self.data_items_table.setColumnCount(5)
            self.data_items_table.setWordWrap(False)
            self.data_items_table.setAlternatingRowColors(True)
            self.data_items_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.data_items_table.verticalHeader().setVisible(False)
            self.data_items_table.setMinimumHeight(260)
            self.data_items_table.cellClicked.connect(self.open_data_item_in_browser)
            make_table_columns_resizable(self.data_items_table)
            self.data_items_table.setColumnWidth(0, 240)
            self.data_items_table.setColumnWidth(1, 160)
            self.data_items_table.setColumnWidth(2, 280)
            data_items_layout.addWidget(self.data_items_table)
            self.trap_child_wheel(self.data_items_table)

            self.application_workspace = QWidget()
            application_layout = QVBoxLayout(self.application_workspace)
            application_layout.setContentsMargins(0, 0, 0, 0)
            app_header = QHBoxLayout()
            self.applications_label = SubtitleLabel()
            self.applications_summary = CaptionLabel()
            app_header.addWidget(self.applications_label)
            app_header.addStretch(1)
            app_header.addWidget(self.applications_summary)
            application_layout.addLayout(app_header)
            self.application_table = TableWidget()
            self.application_table.setColumnCount(4)
            self.application_table.setWordWrap(False)
            self.application_table.setAlternatingRowColors(True)
            self.application_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.application_table.verticalHeader().setVisible(False)
            self.application_table.setMinimumHeight(260)
            make_table_columns_resizable(self.application_table)
            self.application_table.setColumnWidth(0, 260)
            self.application_table.setColumnWidth(1, 130)
            self.application_table.setColumnWidth(2, 220)
            application_layout.addWidget(self.application_table)
            self.trap_child_wheel(self.application_table)

            self.environment_workspace = QWidget()
            environment_layout = QVBoxLayout(self.environment_workspace)
            environment_layout.setContentsMargins(0, 0, 0, 0)
            environment_header = QHBoxLayout()
            self.environment_label = SubtitleLabel()
            self.environment_summary = CaptionLabel()
            environment_header.addWidget(self.environment_label)
            environment_header.addStretch(1)
            environment_header.addWidget(self.environment_summary)
            environment_layout.addLayout(environment_header)
            self.conda_table = TableWidget()
            self.conda_table.setColumnCount(4)
            self.conda_table.setWordWrap(False)
            self.conda_table.setAlternatingRowColors(True)
            self.conda_table.verticalHeader().setVisible(False)
            self.conda_table.setMinimumHeight(260)
            make_table_columns_resizable(self.conda_table)
            self.conda_table.setColumnWidth(0, 200)
            self.conda_table.setColumnWidth(1, 320)
            self.conda_table.setColumnWidth(2, 140)
            environment_layout.addWidget(self.conda_table)
            self.trap_child_wheel(self.conda_table)

            self.recovery_workspace = QWidget()
            recovery_layout = QVBoxLayout(self.recovery_workspace)
            recovery_layout.setContentsMargins(0, 0, 0, 0)
            self.recovery_label = SubtitleLabel()
            self.recovery_text = TextEdit()
            self.recovery_text.setReadOnly(True)
            self.recovery_text.setMinimumHeight(260)
            recovery_layout.addWidget(self.recovery_label)
            recovery_layout.addWidget(self.recovery_text)

            for page in (
                self.data_workspace,
                self.application_workspace,
                self.environment_workspace,
                self.recovery_workspace,
            ):
                self.browser_stack.addWidget(page)
            workspace_layout.addWidget(self.browser_stack)

            self.files_label = SubtitleLabel()
            workspace_layout.addWidget(self.files_label)
            file_workspace = QHBoxLayout()
            file_workspace.setSpacing(18)
            self.archive_preview = ArchiveFilePreviewPanel(t, format_bytes, self)
            self.archive_preview.set_export_enabled(True)
            self.archive_preview.entryExportRequested.connect(self.extract_entry_path)
            self.archive_preview.entryOpenRequested.connect(self.open_entry_with_system_default)
            self.archive_preview.packageLocationRequested.connect(self.open_package_location)
            self.trap_child_wheel(
                self.archive_preview.tree,
                self.archive_preview.list_view,
                self.archive_preview.table,
                self.archive_preview.icon_view,
            )
            file_workspace.addWidget(self.archive_preview)
            workspace_layout.addLayout(file_workspace)
            self.retranslate()

        def open_package_file(self) -> None:
            file_name, _ = QFileDialog.getOpenFileName(
                self,
                t("open_backup_file"),
                "",
                "Backup packages (*.zip *.7z *.iso);;All files (*.*)",
            )
            if file_name:
                self.open_selected_package(Path(file_name))

        def open_package_directory(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, t("open_backup_dir"))
            if directory:
                self.open_selected_package(Path(directory))

        def open_selected_package(self, package_path: Path) -> None:
            logger.info("Opening backup package: %s", package_path)
            try:
                self.load_package(package_path)
            except Exception as exc:
                logger.exception("Failed to load backup package: %s", package_path)
                MessageBox(t("cannot_open_package"), str(exc), self).exec()

        def load_package(self, package_path: Path) -> None:
            try:
                manifest = read_manifest(package_path, configured_temporary_root())
                entries = list_entries(package_path)
            except Exception as exc:
                logger.exception("Unable to inspect backup package: %s", package_path)
                MessageBox(t("cannot_open_package"), str(exc), self).exec()
                return
            self.current_package_path = package_path
            self.current_entries = entries
            self.package_input.setText(str(package_path))
            self.extract_entry_button.setEnabled(bool(entries))
            self.extract_package_button.setEnabled(bool(entries))
            self.entry_groups = {
                "data": [
                    entry for entry in entries if entry.path.replace("\\", "/").startswith("data/")
                ],
                "inventory": [
                    entry
                    for entry in entries
                    if entry.path.replace("\\", "/").startswith("inventory/")
                ],
                "recovery": [
                    entry
                    for entry in entries
                    if not entry.path.replace("\\", "/").startswith(("data/", "inventory/"))
                ],
            }
            entries_by_path = {
                entry.path.replace("\\", "/").strip("/"): entry
                for entry in self.entry_groups["data"]
            }
            self.data_entries_by_item = {}
            for file_entry in manifest.get("files", []):
                item_id = str(file_entry.get("source_item_id") or "")
                relative_path = (
                    str(file_entry.get("relative_path") or "").replace("\\", "/").strip("/")
                )
                entry = entries_by_path.get(relative_path)
                if item_id and entry:
                    self.data_entries_by_item.setdefault(item_id, []).append(entry)
            self.overview_fields["created_at"].setText(str(manifest.get("created_at") or "-"))
            self.overview_fields["mode"].setText(str(manifest.get("mode") or "-"))
            self.overview_fields["format"].setText(str(manifest.get("archive_format") or "-"))
            self.overview_fields["item_count"].setText(str(len(manifest.get("items", []))))
            self.overview_fields["file_count"].setText(str(len(manifest.get("files", []))))
            total_size = sum(entry.size for entry in entries)
            self.overview_fields["package_size"].setText(format_bytes(total_size))
            self.populate_application_table(manifest.get("installed_applications", []))
            self.populate_data_item_table(manifest)
            self.populate_conda_table()
            self.populate_recovery_information(manifest)
            self.browser_section_combo.blockSignals(True)
            self.browser_section_combo.setCurrentIndex(0)
            self.browser_section_combo.blockSignals(False)
            self.change_browser_section(0)

        def populate_application_table(self, applications: list[dict]) -> None:
            self.application_table.clearContents()
            visible_apps = applications[:300]
            self.application_table.setRowCount(len(visible_apps))
            for row, application in enumerate(visible_apps):
                values = [
                    str(application.get("name") or "-"),
                    str(application.get("version") or "-"),
                    str(application.get("publisher") or "-"),
                    str(application.get("install_location") or "-"),
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.application_table.setItem(row, column, item)
            self.applications_summary.setText(
                t("applications_found", shown=len(visible_apps), total=len(applications))
            )

        def populate_data_item_table(self, manifest: dict) -> None:
            files_by_item: dict[str, tuple[int, int]] = {}
            for file_entry in manifest.get("files", []):
                item_id = str(file_entry.get("source_item_id") or "")
                count, total_size = files_by_item.get(item_id, (0, 0))
                files_by_item[item_id] = (count + 1, total_size + int(file_entry.get("size") or 0))

            items = manifest.get("items", [])
            self.data_item_by_row.clear()
            self.data_items_table.clearContents()
            self.data_items_table.setRowCount(len(items))
            for row, item in enumerate(items):
                item_id = str(item.get("id") or "")
                file_count, total_size = files_by_item.get(item_id, (0, 0))
                values = [
                    str(item.get("name") or item_id or "-"),
                    str(item.get("software") or item.get("category") or "-"),
                    str(item.get("path") or "-"),
                    str(file_count),
                    format_bytes(total_size),
                ]
                self.data_item_by_row[row] = item_id
                for column, value in enumerate(values):
                    table_item = QTableWidgetItem(value)
                    table_item.setData(ITEM_ID_ROLE, item_id)
                    table_item.setToolTip(value)
                    table_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.data_items_table.setItem(row, column, table_item)
            self.data_items_summary.setText(t("migrated_data_summary", count=len(items)))

        def open_data_item_in_browser(self, row: int, *_: object) -> None:
            item_id = self.data_item_by_row.get(row)
            if not item_id:
                return
            self.browser_section_combo.setCurrentIndex(0)
            self.archive_preview.filter_input.blockSignals(True)
            self.archive_preview.filter_input.clear()
            self.archive_preview.filter_input.blockSignals(False)
            self.archive_preview.set_entries(self.data_entries_by_item.get(item_id, []))

        def populate_conda_table(self) -> None:
            self.conda_table.clearContents()
            plans = []
            export_results: dict[str, dict] = {}
            if self.current_package_path:
                try:
                    raw = read_entry_bytes(
                        self.current_package_path,
                        "inventory/conda_export_plans.json",
                        limit=2 * 1024 * 1024,
                        temporary_root=configured_temporary_root(),
                    )
                    plans = json.loads(raw.decode("utf-8"))
                except (KeyError, OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
                    plans = []
                    logger.info(
                        "No Conda export plan found in package: %s", self.current_package_path
                    )
                try:
                    raw = read_entry_bytes(
                        self.current_package_path,
                        "inventory/conda_export_results.json",
                        limit=2 * 1024 * 1024,
                        temporary_root=configured_temporary_root(),
                    )
                    export_results = {
                        str(result.get("environment", {}).get("name") or ""): result
                        for result in json.loads(raw.decode("utf-8"))
                    }
                except (KeyError, OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
                    export_results = {}
                    logger.info(
                        "No Conda export results found in package: %s", self.current_package_path
                    )
            self.conda_table.setRowCount(min(len(plans), 200))
            for row, plan in enumerate(plans[:200]):
                environment = plan.get("environment", {})
                result = export_results.get(str(environment.get("name") or ""), {})
                exported_files = result.get("exported_files") or []
                values = [
                    str(environment.get("name") or "-"),
                    str(environment.get("prefix") or "-"),
                    str(len(exported_files)),
                    " ".join(plan.get("restore_command") or []),
                ]
                for column, value in enumerate(values):
                    table_item = QTableWidgetItem(value)
                    table_item.setToolTip(value)
                    table_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.conda_table.setItem(row, column, table_item)
            self.environment_summary.setText(t("conda_environments_found", count=len(plans)))

        def populate_recovery_information(self, manifest: dict) -> None:
            metadata = manifest.get("metadata", {})
            registry_entries = [
                entry
                for entry in self.entry_groups["recovery"]
                if entry.path.startswith("registry/")
            ]
            restore_notes = any(
                entry.path.upper() == "RESTORE.MD" for entry in self.entry_groups["recovery"]
            )
            sensitive = metadata.get("sensitive_items", [])
            self.recovery_text.setPlainText(
                "\n".join(
                    [
                        f"{t('registry_exports')}: {len(registry_entries)}",
                        f"{t('restore_notes')}: {t('available') if restore_notes else t('none')}",
                        f"{t('sensitive_items')}: "
                        f"{', '.join(sensitive) if sensitive else t('none')}",
                        f"{t('package_metadata')}: {len(self.entry_groups['recovery'])}",
                    ]
                )
            )

        def change_browser_section(self, index: int) -> None:
            index = max(0, min(index, 3))
            self.browser_stack.setCurrentIndex(index)
            scope = ("data", "inventory", "inventory", "recovery")[index]
            self.archive_preview.filter_input.blockSignals(True)
            self.archive_preview.filter_input.clear()
            self.archive_preview.filter_input.blockSignals(False)
            self.archive_preview.set_entries(self.entry_groups.get(scope, []))

        def extract_selected_entry(self) -> None:
            entry_path = self.archive_preview.current_entry_path()
            if entry_path:
                self.extract_entry_path(entry_path)

        def extract_entry_path(self, entry_path: str) -> None:
            if not self.current_package_path:
                return
            destination_dir = QFileDialog.getExistingDirectory(self, t("extract_selected_entry"))
            if not destination_dir:
                return
            try:
                target = Path(destination_dir) / Path(entry_path)
                copy_entry_to(
                    self.current_package_path,
                    entry_path,
                    target,
                    configured_temporary_root(),
                )
            except Exception as exc:
                logger.exception("Failed to extract package entry: %s", entry_path)
                MessageBox(t("cannot_open_package"), str(exc), self).exec()
                return
            logger.info("Extracted package entry | entry=%s | destination=%s", entry_path, target)

        def extract_package(self) -> None:
            if not self.current_package_path:
                return
            destination_dir = QFileDialog.getExistingDirectory(self, t("extract_backup_package"))
            if not destination_dir:
                return
            try:
                extract_package_to(
                    self.current_package_path,
                    Path(destination_dir),
                    configured_temporary_root(),
                )
            except Exception as exc:
                logger.exception("Failed to extract backup package: %s", self.current_package_path)
                MessageBox(t("cannot_open_package"), str(exc), self).exec()
                return
            logger.info(
                "Extracted backup package | package=%s | destination=%s",
                self.current_package_path,
                destination_dir,
            )

        def open_package_location(self) -> None:
            if not self.current_package_path:
                return
            location = self.current_package_path.parent
            open_local_path(location, "Opening backup package location")

        def open_entry_with_system_default(self, entry_path: str) -> None:
            if not self.current_package_path:
                return
            try:
                temporary_root = prepare_temporary_root(configured_temporary_root())
                open_root = Path(
                    tempfile.mkdtemp(prefix="back-up-helper-open-", dir=temporary_root)
                )
                extracted = copy_entry_to(
                    self.current_package_path,
                    entry_path,
                    open_root / Path(entry_path),
                    configured_temporary_root(),
                )
                open_local_path(extracted, "Opening extracted package entry with system default")
            except Exception as exc:
                logger.exception("Failed to open package entry: %s", entry_path)
                MessageBox(t("cannot_open_package"), str(exc), self).exec()

        def retranslate(self) -> None:
            super().retranslate()
            if hasattr(self, "open_file_button"):
                self.open_file_button.setText(t("open_backup_file"))
                self.open_dir_button.setText(t("open_backup_dir"))
                self.extract_entry_button.setText(t("extract_selected_entry"))
                self.extract_package_button.setText(t("extract_backup_package"))
                self.manifest_label.setText(t("package_overview"))
                self.overview_created_at_label.setText(t("created_at"))
                self.overview_mode_label.setText(t("mode"))
                self.overview_format_label.setText(t("format"))
                self.overview_item_count_label.setText(t("item_count"))
                self.overview_file_count_label.setText(t("file_count"))
                self.overview_package_size_label.setText(t("package_size"))
                self.applications_label.setText(t("package_applications"))
                self.application_table.setHorizontalHeaderLabels(
                    [t("name"), t("version"), t("publisher"), t("path")]
                )
                self.data_items_label.setText(t("migrated_data"))
                self.data_items_table.setHorizontalHeaderLabels(
                    [t("name"), t("software"), t("source_path"), t("file_count"), t("size")]
                )
                self.environment_label.setText(t("conda_environments"))
                self.conda_table.setHorizontalHeaderLabels(
                    [
                        t("environment"),
                        t("environment_path"),
                        t("exported_files"),
                        t("restore_command"),
                    ]
                )
                self.browser_section_label.setText(t("browser_section"))
                current_index = self.browser_section_combo.currentIndex()
                self.browser_section_combo.blockSignals(True)
                self.browser_section_combo.clear()
                self.browser_section_combo.addItems(
                    [
                        t("package_data"),
                        t("package_applications"),
                        t("conda_environments"),
                        t("package_registry_restore"),
                    ]
                )
                self.browser_section_combo.setCurrentIndex(max(0, current_index))
                self.browser_section_combo.blockSignals(False)
                self.files_label.setText(t("package_contents"))
                self.archive_preview.retranslate()
                self.recovery_label.setText(t("package_registry_restore"))

    class RestorePage(Page):
        def __init__(self) -> None:
            super().__init__("restore_title", "restore_subtitle")
            self.package_path: Path | None = None
            self.restore_root: Path | None = None
            self.restore_plan = None
            self.restore_thread: QThread | None = None
            self.restore_worker: BackupJobWorker | None = None
            _, form_layout = self.add_card()
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(14)
            self.package_label = BodyLabel()
            self.package_input = LineEdit()
            self.package_input.setReadOnly(True)
            self.package_input.setMinimumHeight(40)
            self.restore_root_label = BodyLabel()
            self.restore_input = LineEdit()
            self.restore_input.setReadOnly(True)
            self.restore_input.setMinimumHeight(40)
            self.open_package_button = PushButton(icon("FOLDER"), "")
            self.open_package_button.setMaximumWidth(210)
            self.open_package_button.clicked.connect(self.open_package)
            self.choose_restore_button = PushButton(icon("SAVE"), "")
            self.choose_restore_button.setMaximumWidth(210)
            self.choose_restore_button.clicked.connect(self.choose_restore_root)
            self.plan_button = PrimaryPushButton(icon("SEARCH"), "")
            self.plan_button.setMinimumHeight(46)
            self.plan_button.setMaximumWidth(240)
            self.plan_button.clicked.connect(self.create_plan)
            self.conflict_policy_label = BodyLabel()
            self.conflict_policy_combo = ComboBox()
            self.conflict_policy_combo.addItems(["skip", "overwrite"])
            self.restore_confirm_checkbox = CheckBox()
            self.restore_button = PrimaryPushButton(icon("PLAY", "RETURN"), "")
            self.restore_button.setMaximumWidth(240)
            self.restore_button.setEnabled(False)
            self.restore_button.clicked.connect(self.execute_restore)
            grid.addWidget(self.package_label, 0, 0)
            grid.addWidget(self.package_input, 0, 1)
            grid.addWidget(self.open_package_button, 0, 2)
            grid.addWidget(self.restore_root_label, 1, 0)
            grid.addWidget(self.restore_input, 1, 1)
            grid.addWidget(self.choose_restore_button, 1, 2)
            grid.addWidget(self.conflict_policy_label, 2, 0)
            grid.addWidget(self.conflict_policy_combo, 2, 1)
            grid.addWidget(self.plan_button, 3, 1, alignment=Qt.AlignmentFlag.AlignRight)
            grid.addWidget(self.restore_confirm_checkbox, 4, 1)
            grid.addWidget(self.restore_button, 4, 2, alignment=Qt.AlignmentFlag.AlignRight)
            form_layout.addLayout(grid)

            _, package_preview_layout = self.add_card()
            restore_preview_header = QHBoxLayout()
            self.restore_package_contents_label = SubtitleLabel()
            self.restore_package_summary = CaptionLabel()
            self.restore_package_summary.setWordWrap(True)
            restore_preview_header.addWidget(self.restore_package_contents_label)
            restore_preview_header.addStretch(1)
            restore_preview_header.addWidget(self.restore_package_summary)
            package_preview_layout.addLayout(restore_preview_header)
            restore_preview_row = QHBoxLayout()
            restore_preview_row.setSpacing(18)
            self.restore_archive_preview = ArchiveFilePreviewPanel(t, format_bytes, self)
            self.restore_archive_preview.set_export_enabled(False)
            self.restore_archive_preview.entrySelected.connect(self.preview_restore_package_entry)
            self.restore_archive_preview.entryActivated.connect(self.preview_restore_package_entry)
            self.restore_entry_preview = TextEdit()
            self.restore_entry_preview.setReadOnly(True)
            self.restore_entry_preview.setMinimumHeight(390)
            self.restore_entry_preview.setPlainText(t("select_file_to_preview"))
            restore_preview_row.addWidget(self.restore_archive_preview, 3)
            restore_preview_row.addWidget(self.restore_entry_preview, 2)
            package_preview_layout.addLayout(restore_preview_row)
            self.trap_child_wheel(
                self.restore_archive_preview.tree,
                self.restore_archive_preview.list_view,
                self.restore_archive_preview.table,
                self.restore_archive_preview.icon_view,
            )

            _, plan_layout = self.add_card()
            self.restore_plan_label = SubtitleLabel()
            self.restore_plan_summary = CaptionLabel()
            self.restore_plan_summary.setWordWrap(True)
            plan_layout.addWidget(self.restore_plan_label)
            plan_layout.addWidget(self.restore_plan_summary)
            self.plan_table = TableWidget()
            self.plan_table.setColumnCount(4)
            self.plan_table.setWordWrap(False)
            self.plan_table.verticalHeader().setVisible(False)
            self.plan_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.plan_table.setMinimumHeight(520)
            make_table_columns_resizable(self.plan_table)
            self.plan_table.setColumnWidth(0, 280)
            self.plan_table.setColumnWidth(1, 360)
            self.plan_table.setColumnWidth(2, 110)
            plan_layout.addWidget(self.plan_table, 1)
            self.trap_child_wheel(self.plan_table)
            _, progress_layout = self.add_card()
            self.restore_progress_label = SubtitleLabel()
            self.restore_stop_button = PushButton(icon("CANCEL"), "")
            self.restore_stop_button.setMaximumWidth(120)
            self.restore_stop_button.setEnabled(False)
            self.restore_stop_button.clicked.connect(self.request_stop_restore)
            restore_progress_header = QHBoxLayout()
            restore_progress_header.addWidget(self.restore_progress_label)
            restore_progress_header.addStretch(1)
            restore_progress_header.addWidget(self.restore_stop_button)
            self.restore_progress_bar = QProgressBar()
            self.restore_progress_bar.setRange(0, 100)
            self.restore_progress_bar.setValue(0)
            self.restore_log_text = TextEdit()
            self.restore_log_text.setReadOnly(True)
            self.restore_log_text.setMaximumHeight(130)
            progress_layout.addLayout(restore_progress_header)
            progress_layout.addWidget(self.restore_progress_bar)
            progress_layout.addWidget(self.restore_log_text)
            self.retranslate()

        def open_package(self) -> None:
            file_name, _ = QFileDialog.getOpenFileName(
                self,
                t("open_backup_file"),
                "",
                "Backup packages (*.zip *.7z *.iso);;All files (*.*)",
            )
            if file_name:
                self.package_path = Path(file_name)
                self.package_input.setText(file_name)
                self.load_restore_package_preview()

        def load_restore_package_preview(self) -> None:
            if not self.package_path:
                return
            try:
                manifest = read_manifest(self.package_path, configured_temporary_root())
                entries = list_entries(self.package_path)
            except Exception as exc:
                self.restore_package_summary.setText(str(exc))
                self.restore_archive_preview.set_entries([])
                return
            data_entries = [
                entry for entry in entries if entry.path.replace("\\", "/").startswith("data/")
            ]
            applications = manifest.get("installed_applications", [])
            registry_entries = [
                entry for entry in entries if entry.path.replace("\\", "/").startswith("registry/")
            ]
            self.restore_archive_preview.set_entries(data_entries)
            self.restore_package_summary.setText(
                "  |  ".join(
                    [
                        t("applications_count", count=len(applications)),
                        t("file_count") + f": {len(data_entries)}",
                        t("registry_exports") + f": {len(registry_entries)}",
                    ]
                )
            )
            self.restore_entry_preview.setPlainText(t("select_file_to_preview"))

        def preview_restore_package_entry(self, entry_path: str) -> None:
            if not self.package_path:
                return
            try:
                if Path(entry_path).suffix.lower() in IMAGE_EXTENSIONS:
                    self.restore_entry_preview.setPlainText(t("image_preview_failed"))
                    return
                self.restore_entry_preview.setPlainText(
                    preview_entry_text(
                        self.package_path,
                        entry_path,
                        configured_temporary_root(),
                    )
                )
            except Exception as exc:
                self.restore_entry_preview.setPlainText(str(exc))

        def choose_restore_root(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, t("choose_restore_root"))
            if directory:
                self.restore_root = Path(directory)
                self.restore_input.setText(directory)

        def create_plan(self) -> None:
            if not self.package_path or not self.restore_root:
                MessageBox(t("missing_path"), t("missing_restore_body"), self).exec()
                return
            try:
                plan = build_restore_plan(
                    self.package_path,
                    self.restore_root,
                    configured_temporary_root(),
                )
            except Exception as exc:
                MessageBox(t("cannot_create_restore_plan"), str(exc), self).exec()
                return
            self.restore_plan = plan
            self.restore_confirm_checkbox.setChecked(False)
            self.restore_button.setEnabled(True)
            conflicts = sum(1 for operation in plan.operations if operation.conflict)
            self.restore_plan_summary.setText(
                " · ".join(
                    [
                        f"{t('operations')}: {len(plan.operations)}",
                        f"{t('conflicts')}: {conflicts}",
                        f"{t('registry_keys')}: {len(plan.registry_keys)}",
                        f"{t('sensitive_items')}: "
                        f"{', '.join(plan.sensitive_item_ids) or t('none')}",
                    ]
                )
            )
            self.plan_table.clearContents()
            self.plan_table.setRowCount(min(len(plan.operations), 300))
            for row, operation in enumerate(plan.operations[:300]):
                values = [
                    operation.source_relative_path,
                    str(operation.destination_path),
                    t("conflict") if operation.conflict else t("ready"),
                    operation.source_item_id,
                ]
                for column, value in enumerate(values):
                    table_item = QTableWidgetItem(value)
                    table_item.setToolTip(value)
                    table_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.plan_table.setItem(row, column, table_item)

        def execute_restore(self) -> None:
            if not self.restore_plan:
                return
            if not self.restore_confirm_checkbox.isChecked():
                MessageBox(
                    t("restore_confirmation_title"),
                    t("restore_confirmation_body"),
                    self,
                ).exec()
                return
            if self.restore_thread and self.restore_thread.isRunning():
                return
            plan = self.restore_plan
            policy = ("skip", "overwrite")[self.conflict_policy_combo.currentIndex()]
            temporary_root = configured_temporary_root()
            self.restore_log_text.clear()
            self.log_restore(t("restore_starting"))

            def task(progress, cancel_event):
                return execute_restore_plan(
                    plan,
                    policy,
                    progress,
                    temporary_root,
                    cancel_event,
                )

            self.restore_button.setEnabled(False)
            self.restore_stop_button.setEnabled(True)
            self.restore_progress_bar.setRange(0, 100)
            self.restore_progress_bar.setValue(0)
            self.restore_thread = QThread(self)
            self.restore_worker = BackupJobWorker(task)
            self.restore_worker.moveToThread(self.restore_thread)
            self.restore_thread.started.connect(self.restore_worker.run)
            self.restore_worker.progress.connect(self.on_restore_progress)
            self.restore_worker.finished.connect(self.on_restore_finished)
            self.restore_worker.failed.connect(self.on_restore_failed)
            self.restore_worker.cancelled.connect(self.on_restore_cancelled)
            self.restore_worker.finished.connect(self.restore_thread.quit)
            self.restore_worker.failed.connect(self.restore_thread.quit)
            self.restore_worker.cancelled.connect(self.restore_thread.quit)
            self.restore_worker.finished.connect(self.restore_worker.deleteLater)
            self.restore_worker.failed.connect(self.restore_worker.deleteLater)
            self.restore_worker.cancelled.connect(self.restore_worker.deleteLater)
            self.restore_thread.finished.connect(self.restore_thread.deleteLater)
            self.restore_thread.finished.connect(self.cleanup_restore_worker)
            self.restore_thread.start()

        def log_restore(self, message: str) -> None:
            logger.info(message)
            self.restore_log_text.append(message)

        def on_restore_progress(self, message: str, current: int, total: int) -> None:
            if total <= 0 or current < 0:
                self.restore_progress_bar.setRange(0, 0)
            elif total:
                if (
                    self.restore_progress_bar.minimum() != 0
                    or self.restore_progress_bar.maximum() != 100
                ):
                    self.restore_progress_bar.setRange(0, 100)
                self.restore_progress_bar.setValue(max(0, min(100, int(current / total * 100))))
            self.log_restore(message)

        def on_restore_finished(self, result: object) -> None:
            self.restore_progress_bar.setValue(100)
            self.log_restore(
                t(
                    "restore_finished_summary",
                    restored=result.restored,
                    skipped=result.skipped_conflicts,
                    unsafe=result.skipped_unsafe,
                )
            )

        def on_restore_failed(self, message: str) -> None:
            self.log_restore(f"Restore failed: {message}")
            MessageBox(t("restore_failed"), message, self).exec()

        def on_restore_cancelled(self, message: str) -> None:
            self.restore_progress_bar.setRange(0, 100)
            self.restore_progress_bar.setValue(0)
            self.log_restore(message)
            MessageBox(t("backup_cancelled"), t("backup_cancelled_body"), self).exec()

        def request_stop_restore(self) -> None:
            if not self.restore_worker or not confirm_stop_operation(self):
                return
            self.restore_worker.cancel()
            self.restore_stop_button.setEnabled(False)
            self.log_restore(t("stop_operation"))

        def cleanup_restore_worker(self) -> None:
            self.restore_thread = None
            self.restore_worker = None
            self.restore_button.setEnabled(self.restore_plan is not None)
            self.restore_stop_button.setEnabled(False)

        def shutdown_workers(self) -> None:
            if self.restore_thread and self.restore_thread.isRunning():
                logger.info("Waiting for active restore worker before closing")
                self.restore_thread.quit()
                self.restore_thread.wait(1500)

        def retranslate(self) -> None:
            super().retranslate()
            if hasattr(self, "package_label"):
                self.package_label.setText(t("backup_package"))
                self.restore_root_label.setText(t("restore_root"))
                self.open_package_button.setText(t("open_backup_file"))
                self.choose_restore_button.setText(t("choose_restore_root"))
                self.plan_button.setText(t("create_restore_plan"))
                self.conflict_policy_label.setText(t("conflict_policy"))
                self.conflict_policy_combo.setItemText(0, t("conflict_skip"))
                self.conflict_policy_combo.setItemText(1, t("conflict_overwrite"))
                self.restore_confirm_checkbox.setText(t("restore_confirm"))
                self.restore_button.setText(t("execute_restore"))
                self.restore_package_contents_label.setText(t("package_contents"))
                self.restore_archive_preview.retranslate()
                if not self.restore_archive_preview.current_entry_path():
                    self.restore_entry_preview.setPlainText(t("select_file_to_preview"))
                self.restore_plan_label.setText(t("restore_plan"))
                self.restore_progress_label.setText(t("restore_progress"))
                self.restore_stop_button.setText(t("stop"))
                self.plan_table.setHorizontalHeaderLabels(
                    [t("source"), t("destination"), t("state"), t("source_item")]
                )

    class SettingsPage(Page):
        def __init__(
            self,
            on_theme_changed,
            on_language_changed,
            on_developer_mode_changed,
            on_logging_changed,
        ) -> None:
            self.on_theme_changed = on_theme_changed
            self.on_language_changed = on_language_changed
            self.on_developer_mode_changed = on_developer_mode_changed
            self.on_logging_changed = on_logging_changed
            super().__init__("settings_title", "settings_subtitle")

            # Appearance and language are the settings users change most often.
            _, appearance_layout = self.add_card()
            self.appearance_label = SubtitleLabel()
            appearance_layout.addWidget(self.appearance_label)
            theme_row = QHBoxLayout()
            theme_row.setSpacing(12)
            self.theme_label = BodyLabel()
            self.theme_group = QButtonGroup(self)
            self.theme_buttons: dict[str, CheckBox] = {}
            for value in ["auto", "light", "dark"]:
                button = CheckBox()
                button.setProperty("themeValue", value)
                self.theme_group.addButton(button)
                self.theme_buttons[value] = button
                theme_row.addWidget(button)
                button.setChecked(settings.theme == value)
            self.theme_group.buttonClicked.connect(self.theme_button_clicked)
            theme_row.insertWidget(0, self.theme_label)
            theme_row.addStretch(1)
            appearance_layout.addLayout(theme_row)

            language_row = QHBoxLayout()
            language_row.setSpacing(12)
            self.language_label = BodyLabel()
            self.language_combo = ComboBox()
            for code, name in SUPPORTED_LANGUAGES.items():
                self.language_combo.addItem(name, userData=code)
            self.language_combo.setCurrentIndex(list(SUPPORTED_LANGUAGES).index(settings.language))
            self.language_combo.currentIndexChanged.connect(self.language_changed)
            self.language_hint = CaptionLabel()
            self.language_hint.setWordWrap(True)
            language_row.addWidget(self.language_label)
            language_row.addWidget(self.language_combo)
            language_row.addStretch(1)
            appearance_layout.addLayout(language_row)
            appearance_layout.addWidget(self.language_hint)

            _, backup_layout = self.add_card()
            self.backup_security_label = SubtitleLabel()
            self.encrypt_default_checkbox = CheckBox()
            self.encrypt_default_checkbox.setChecked(settings.encrypt_by_default)
            self.encrypt_default_checkbox.stateChanged.connect(self.encrypt_default_changed)
            self.sensitive_confirm_checkbox = CheckBox()
            self.sensitive_confirm_checkbox.setChecked(settings.sensitive_confirm)
            self.sensitive_confirm_checkbox.stateChanged.connect(self.sensitive_confirm_changed)
            backup_layout.addWidget(self.backup_security_label)
            backup_layout.addWidget(self.encrypt_default_checkbox)
            backup_layout.addWidget(self.sensitive_confirm_checkbox)

            _, storage_layout = self.add_card()
            self.storage_label = SubtitleLabel()
            self.temporary_root_label = BodyLabel()
            self.temporary_root_input = LineEdit()
            self.temporary_root_input.setReadOnly(True)
            self.temporary_root_input.setMinimumHeight(38)
            self.choose_temporary_root_button = PushButton(icon("FOLDER"), "")
            self.choose_temporary_root_button.setMaximumWidth(180)
            self.choose_temporary_root_button.clicked.connect(self.choose_temporary_root)
            self.reset_temporary_root_button = PushButton(icon("SYNC", "CLEAR_SELECTION"), "")
            self.reset_temporary_root_button.setMaximumWidth(170)
            self.reset_temporary_root_button.clicked.connect(self.reset_temporary_root)
            temporary_root_row = QHBoxLayout()
            temporary_root_row.setSpacing(12)
            temporary_root_row.addWidget(self.temporary_root_label)
            temporary_root_row.addWidget(self.temporary_root_input, 1)
            temporary_root_row.addWidget(self.choose_temporary_root_button)
            temporary_root_row.addWidget(self.reset_temporary_root_button)

            self.settings_directory_label = BodyLabel()
            self.settings_directory_input = LineEdit()
            self.settings_directory_input.setReadOnly(True)
            self.settings_directory_input.setMinimumHeight(38)
            self.choose_settings_directory_button = PushButton(icon("FOLDER"), "")
            self.choose_settings_directory_button.setMaximumWidth(180)
            self.choose_settings_directory_button.clicked.connect(self.choose_settings_directory)
            self.reset_settings_directory_button = PushButton(icon("SYNC", "CLEAR_SELECTION"), "")
            self.reset_settings_directory_button.setMaximumWidth(180)
            self.reset_settings_directory_button.clicked.connect(self.reset_settings_directory)
            settings_directory_row = QHBoxLayout()
            settings_directory_row.setSpacing(12)
            settings_directory_row.addWidget(self.settings_directory_label)
            settings_directory_row.addWidget(self.settings_directory_input, 1)
            settings_directory_row.addWidget(self.choose_settings_directory_button)
            settings_directory_row.addWidget(self.reset_settings_directory_button)
            storage_layout.addWidget(self.storage_label)
            storage_layout.addLayout(temporary_root_row)
            storage_layout.addLayout(settings_directory_row)

            _, runtime_layout = self.add_card()
            self.runtime_logs_label = SubtitleLabel()
            self.runtime_logs_hint = CaptionLabel()
            self.runtime_logs_hint.setWordWrap(True)
            self.persist_runtime_logs_checkbox = CheckBox()
            self.persist_runtime_logs_checkbox.setChecked(settings.persist_runtime_logs)
            self.persist_runtime_logs_checkbox.stateChanged.connect(
                self.persist_runtime_logs_changed
            )
            self.runtime_log_directory_label = BodyLabel()
            self.runtime_log_directory_input = LineEdit()
            self.runtime_log_directory_input.setReadOnly(True)
            self.runtime_log_directory_input.setMinimumHeight(38)
            self.open_runtime_log_directory_button = PushButton(icon("FOLDER"), "")
            self.open_runtime_log_directory_button.setMaximumWidth(180)
            self.open_runtime_log_directory_button.clicked.connect(self.open_runtime_log_directory)
            runtime_directory_row = QHBoxLayout()
            runtime_directory_row.setSpacing(12)
            runtime_directory_row.addWidget(self.runtime_log_directory_label)
            runtime_directory_row.addWidget(self.runtime_log_directory_input, 1)
            runtime_directory_row.addWidget(self.open_runtime_log_directory_button)
            runtime_layout.addWidget(self.runtime_logs_label)
            runtime_layout.addWidget(self.runtime_logs_hint)
            runtime_layout.addWidget(self.persist_runtime_logs_checkbox)
            runtime_layout.addLayout(runtime_directory_row)

            _, advanced_layout = self.add_card()
            self.advanced_label = SubtitleLabel()
            self.developer_mode_checkbox = CheckBox()
            self.developer_mode_checkbox.setChecked(settings.developer_mode)
            self.developer_mode_checkbox.stateChanged.connect(self.developer_mode_changed)
            self.developer_mode_hint = CaptionLabel()
            self.developer_mode_hint.setWordWrap(True)
            advanced_layout.addWidget(self.advanced_label)
            advanced_layout.addWidget(self.developer_mode_checkbox)
            advanced_layout.addWidget(self.developer_mode_hint)

            # Keep project information last: it is reference material, not a preference.
            _, about_layout = self.add_card()
            self.about_label = SubtitleLabel()
            self.about_hint = CaptionLabel()
            self.about_hint.setWordWrap(True)
            about_layout.addWidget(self.about_label)
            about_layout.addWidget(self.about_hint)
            link_grid = QGridLayout()
            link_grid.setHorizontalSpacing(10)
            link_grid.setVerticalSpacing(10)
            self.project_home_button = PushButton(icon("GITHUB", "LINK"), "")
            self.license_button = PushButton(icon("DOCUMENT", "LINK"), "")
            for button in (self.project_home_button, self.license_button):
                button.setMaximumWidth(220)
            self.project_home_button.clicked.connect(
                lambda: open_url("https://github.com/whtry/backUpHelper")
            )
            self.license_button.clicked.connect(
                lambda: open_url("https://github.com/whtry/backUpHelper/blob/main/LICENSE")
            )
            link_grid.addWidget(self.project_home_button, 0, 0)
            link_grid.addWidget(self.license_button, 0, 1)
            self.open_source_label = BodyLabel()
            link_grid.addWidget(self.open_source_label, 1, 0, 1, 2)
            self.open_source_buttons: list[PushButton] = []
            links = [
                ("Python", "https://www.python.org/"),
                ("PySide6 / Qt", "https://doc.qt.io/qtforpython-6/"),
                ("QFluentWidgets", "https://github.com/zhiyiYo/PyQt-Fluent-Widgets"),
                ("PyInstaller", "https://pyinstaller.org/"),
                ("7-Zip", "https://www.7-zip.org/"),
                ("py7zr", "https://github.com/miurahr/py7zr"),
                ("pycdlib", "https://clalancette.github.io/pycdlib/"),
                ("cryptography", "https://cryptography.io/"),
                ("Bootstrap Icons", "https://icons.getbootstrap.com/"),
                ("pytest", "https://pytest.org/"),
                ("ruff", "https://docs.astral.sh/ruff/"),
            ]
            for index, (name, url) in enumerate(links):
                button = PushButton(name)
                button.setMaximumWidth(220)
                button.clicked.connect(lambda checked=False, target=url: open_url(target))
                self.open_source_buttons.append(button)
                link_grid.addWidget(button, 2 + index // 3, index % 3)
            about_layout.addLayout(link_grid)

            self.support_label = SubtitleLabel()
            self.support_hint = CaptionLabel()
            self.support_hint.setWordWrap(True)
            self.support_qr_label = QLabel()
            self.support_qr_label.setFixedSize(176, 176)
            self.support_qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.support_qr_label.setToolTip("Alipay")
            support_text_layout = QVBoxLayout()
            support_text_layout.setSpacing(8)
            support_text_layout.addWidget(self.support_label)
            support_text_layout.addWidget(self.support_hint)
            support_text_layout.addStretch(1)
            support_row = QHBoxLayout()
            support_row.setSpacing(22)
            support_row.addLayout(support_text_layout, 1)
            support_row.addWidget(self.support_qr_label)
            about_layout.addSpacing(8)
            about_layout.addLayout(support_row)
            self.load_support_qr()
            self.root_layout.addStretch(1)
            self.retranslate()

        def load_support_qr(self) -> None:
            pixmap = QPixmap(str(asset_path("alipay.jpg")))
            if pixmap.isNull():
                self.support_qr_label.clear()
                return
            self.support_qr_label.setPixmap(
                pixmap.scaled(
                    164,
                    164,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

        def theme_button_clicked(self, button: CheckBox) -> None:
            for other in self.theme_group.buttons():
                if other is not button:
                    other.setChecked(False)
            value = button.property("themeValue")
            settings.theme = value
            save_settings(settings)
            self.on_theme_changed(value)

        def language_changed(self) -> None:
            code = self.language_combo.currentData()
            if code:
                settings.language = code
                save_settings(settings)
                self.on_language_changed()

        def encrypt_default_changed(self) -> None:
            settings.encrypt_by_default = self.encrypt_default_checkbox.isChecked()
            save_settings(settings)

        def sensitive_confirm_changed(self) -> None:
            settings.sensitive_confirm = self.sensitive_confirm_checkbox.isChecked()
            save_settings(settings)

        def persist_runtime_logs_changed(self) -> None:
            settings.persist_runtime_logs = self.persist_runtime_logs_checkbox.isChecked()
            save_settings(settings)
            self.on_logging_changed(settings.persist_runtime_logs)

        def open_runtime_log_directory(self) -> None:
            directory = runtime_log_directory(configured_temporary_root())
            directory.mkdir(parents=True, exist_ok=True)
            open_local_path(directory, "Opening runtime log directory")

        def developer_mode_changed(self) -> None:
            settings.developer_mode = self.developer_mode_checkbox.isChecked()
            save_settings(settings)
            self.on_developer_mode_changed(settings.developer_mode)

        def choose_temporary_root(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, t("choose_temporary_root"))
            if not directory:
                return
            settings.temporary_root = str(Path(directory))
            save_settings(settings)
            self.on_logging_changed(settings.persist_runtime_logs)
            self.sync_from_settings()

        def reset_temporary_root(self) -> None:
            settings.temporary_root = None
            save_settings(settings)
            self.on_logging_changed(settings.persist_runtime_logs)
            self.sync_from_settings()

        def choose_settings_directory(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, t("choose_settings_directory"))
            if not directory:
                return
            if not set_settings_directory(settings, Path(directory)):
                MessageBox(t("settings_title"), t("settings_directory_update_failed"), self).exec()
                return
            logger.info("Configuration directory changed to: %s", settings_directory())
            self.sync_from_settings()

        def reset_settings_directory(self) -> None:
            if not reset_settings_directory(settings):
                MessageBox(t("settings_title"), t("settings_directory_update_failed"), self).exec()
                return
            logger.info("Configuration directory reset to: %s", settings_directory())
            self.sync_from_settings()

        def sync_from_settings(self) -> None:
            if not hasattr(self, "theme_buttons"):
                return
            for value, button in self.theme_buttons.items():
                button.blockSignals(True)
                button.setChecked(settings.theme == value)
                button.blockSignals(False)
            languages = list(SUPPORTED_LANGUAGES)
            index = languages.index(settings.language) if settings.language in languages else 0
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(index)
            self.language_combo.blockSignals(False)
            self.encrypt_default_checkbox.blockSignals(True)
            self.encrypt_default_checkbox.setChecked(settings.encrypt_by_default)
            self.encrypt_default_checkbox.blockSignals(False)
            self.sensitive_confirm_checkbox.blockSignals(True)
            self.sensitive_confirm_checkbox.setChecked(settings.sensitive_confirm)
            self.sensitive_confirm_checkbox.blockSignals(False)
            self.persist_runtime_logs_checkbox.blockSignals(True)
            self.persist_runtime_logs_checkbox.setChecked(settings.persist_runtime_logs)
            self.persist_runtime_logs_checkbox.blockSignals(False)
            self.developer_mode_checkbox.blockSignals(True)
            self.developer_mode_checkbox.setChecked(settings.developer_mode)
            self.developer_mode_checkbox.blockSignals(False)
            self.temporary_root_input.setText(
                settings.temporary_root or t("temporary_root_system")
            )
            self.runtime_log_directory_input.setText(
                str(runtime_log_directory(configured_temporary_root()))
            )
            self.settings_directory_input.setText(str(settings_directory()))

        def retranslate(self) -> None:
            super().retranslate()
            if hasattr(self, "appearance_label"):
                self.sync_from_settings()
                self.appearance_label.setText(t("appearance"))
                self.theme_label.setText(t("theme"))
                self.theme_buttons["auto"].setText(t("theme_auto"))
                self.theme_buttons["light"].setText(t("theme_light"))
                self.theme_buttons["dark"].setText(t("theme_dark"))
                self.language_label.setText(t("language"))
                self.language_hint.setText(t("language_hint"))
                self.backup_security_label.setText(t("backup_security"))
                self.encrypt_default_checkbox.setText(t("encrypt_by_default"))
                self.sensitive_confirm_checkbox.setText(t("sensitive_confirm"))
                self.storage_label.setText(t("storage"))
                self.temporary_root_label.setText(t("temporary_root"))
                self.choose_temporary_root_button.setText(t("choose_temporary_root"))
                self.reset_temporary_root_button.setText(t("reset_temporary_root"))
                self.settings_directory_label.setText(t("settings_directory"))
                self.choose_settings_directory_button.setText(t("choose_settings_directory"))
                self.reset_settings_directory_button.setText(t("reset_settings_directory"))
                self.runtime_logs_label.setText(t("runtime_logs"))
                self.runtime_logs_hint.setText(t("runtime_logs_hint"))
                self.persist_runtime_logs_checkbox.setText(t("persist_runtime_logs"))
                self.runtime_log_directory_label.setText(t("runtime_log_directory"))
                self.open_runtime_log_directory_button.setText(t("open_log_directory"))
                self.advanced_label.setText(t("advanced"))
                self.developer_mode_checkbox.setText(t("developer_mode"))
                self.developer_mode_hint.setText(t("developer_mode_hint"))
                self.about_label.setText(t("about_project"))
                self.about_hint.setText(t("about_project_hint"))
                self.project_home_button.setText(t("project_home"))
                self.license_button.setText(t("license_file"))
                self.open_source_label.setText(t("open_source_projects"))
                self.support_label.setText(t("support_project"))
                self.support_hint.setText(t("support_project_hint"))

    class MainWindow(FluentWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(t("app_title"))
            icon_path = app_icon_path()
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
            self.resize(1120, 720)
            self.setMinimumSize(900, 620)

            self.smart = SmartBackupPage()
            self.full = FullBackupPage()
            self.browser = PackageBrowserPage()
            self.restore = RestorePage()
            self.settings_page = SettingsPage(
                self.change_theme,
                self.retranslate_pages,
                self.change_developer_mode,
                self.change_runtime_log_persistence,
            )
            self.pages = [
                self.smart,
                self.full,
                self.browser,
                self.restore,
                self.settings_page,
            ]
            for page, name in [
                (self.smart, "smartBackupPage"),
                (self.full, "fullBackupPage"),
                (self.browser, "packageBrowserPage"),
                (self.restore, "restorePage"),
                (self.settings_page, "settingsPage"),
            ]:
                page.setObjectName(name)

            self.addSubInterface(self.smart, icon("UPDATE"), t("nav_smart"))
            self.addSubInterface(self.full, icon("SAVE_AS"), t("nav_full"))
            self.addSubInterface(self.browser, icon("ZIP_FOLDER"), t("nav_browser"))
            self.addSubInterface(self.restore, icon("HISTORY"), t("nav_restore"))
            self.theme_nav_item = self.navigationInterface.addItem(
                routeKey="themeAction",
                icon=self.theme_action_icon(),
                text=self.theme_action_text(),
                onClick=self.cycle_theme,
                selectable=False,
                position=NavigationItemPosition.BOTTOM,
            )
            self.language_nav_item = self.navigationInterface.addItem(
                routeKey="languageAction",
                icon=self.language_action_icon(),
                text=self.language_action_text(),
                onClick=self.cycle_language,
                selectable=False,
                position=NavigationItemPosition.BOTTOM,
            )
            self.addSubInterface(
                self.settings_page,
                icon("SETTING", "APPLICATION"),
                t("nav_settings"),
                position=NavigationItemPosition.BOTTOM,
            )
            self.apply_developer_mode()
            self.restore_window_placement()

        def change_theme(self, value: str) -> None:
            apply_theme(value)
            self.update_nav_action_labels()

        def change_developer_mode(self, enabled: bool) -> None:
            settings.developer_mode = enabled
            self.apply_developer_mode()

        def change_runtime_log_persistence(self, enabled: bool) -> None:
            log_path = configure_application_logging(enabled, configured_temporary_root())
            if log_path:
                logger.info("Persistent runtime logging enabled")
            else:
                logger.info("Persistent runtime logging disabled")

        def apply_developer_mode(self) -> None:
            enabled = settings.developer_mode
            for route_key in ("fullBackupPage", "restorePage"):
                nav_item = self.navigationInterface.widget(route_key)
                if nav_item:
                    nav_item.setVisible(enabled)
            self.full.setEnabled(enabled)
            self.restore.setEnabled(enabled)
            if not enabled and self.stackedWidget.currentWidget() in (self.full, self.restore):
                self.switchTo(self.smart)
                self.navigationInterface.setCurrentItem(self.smart.objectName())
            self.update_nav_action_labels()

        def theme_action_icon(self):
            return {
                "auto": icon("PALETTE"),
                "light": icon("BRIGHTNESS"),
                "dark": icon("QUIET_HOURS"),
            }.get(settings.theme, icon("PALETTE"))

        def theme_action_text(self) -> str:
            return f"{t('nav_theme')} · {t(f'theme_{settings.theme}')}"

        def language_action_icon(self):
            return {
                "zh_CN": icon("LANGUAGE"),
                "en_US": icon("GLOBE"),
            }.get(settings.language, icon("LANGUAGE"))

        def language_action_text(self) -> str:
            language_name = SUPPORTED_LANGUAGES.get(settings.language, settings.language)
            return f"{t('nav_language')} · {language_name}"

        def update_nav_action_labels(self) -> None:
            for route_key, text_key in [
                ("smartBackupPage", "nav_smart"),
                ("fullBackupPage", "nav_full"),
                ("packageBrowserPage", "nav_browser"),
                ("restorePage", "nav_restore"),
                ("settingsPage", "nav_settings"),
            ]:
                nav_item = self.navigationInterface.widget(route_key)
                if nav_item:
                    nav_item.setText(t(text_key))
            if hasattr(self, "theme_nav_item"):
                self.theme_nav_item.setText(self.theme_action_text())
                self.theme_nav_item.setIcon(self.theme_action_icon())
                self.theme_nav_item.setToolTip(self.theme_action_text())
            if hasattr(self, "language_nav_item"):
                self.language_nav_item.setText(self.language_action_text())
                self.language_nav_item.setIcon(self.language_action_icon())
                self.language_nav_item.setToolTip(self.language_action_text())
            self.settings_page.sync_from_settings()

        def cycle_theme(self) -> None:
            values = ["auto", "light", "dark"]
            current = settings.theme if settings.theme in values else "auto"
            settings.theme = values[(values.index(current) + 1) % len(values)]
            save_settings(settings)
            apply_theme(settings.theme)
            self.update_nav_action_labels()

        def cycle_language(self) -> None:
            values = list(SUPPORTED_LANGUAGES)
            current = settings.language if settings.language in values else values[0]
            settings.language = values[(values.index(current) + 1) % len(values)]
            save_settings(settings)
            self.retranslate_pages()
            self.update_nav_action_labels()

        def retranslate_pages(self) -> None:
            self.setWindowTitle(t("app_title"))
            for page in self.pages:
                page.retranslate()
            self.update_nav_action_labels()

        def restore_window_placement(self) -> None:
            restored = False
            if settings.window_geometry:
                geometry = QByteArray.fromBase64(settings.window_geometry.encode("ascii"))
                restored = self.restoreGeometry(geometry)
            if settings.window_state:
                try:
                    self.setWindowState(Qt.WindowState(int(settings.window_state)))
                except ValueError:
                    pass
            if not restored:
                screen = QApplication.primaryScreen()
                if screen:
                    frame = self.frameGeometry()
                    frame.moveCenter(screen.availableGeometry().center())
                    self.move(frame.topLeft())
            self.ensure_window_visible()
            try:
                self.switchTo(self.smart)
                self.navigationInterface.setCurrentItem(self.smart.objectName())
            except Exception:
                pass

        def ensure_window_visible(self) -> None:
            screen = self.screen() or QApplication.primaryScreen()
            if not screen:
                return
            available = screen.availableGeometry()
            frame = self.frameGeometry()
            if frame.width() > available.width() or frame.height() > available.height():
                self.resize(
                    min(self.width(), max(940, available.width() - 80)),
                    min(self.height(), max(640, available.height() - 80)),
                )
                frame = self.frameGeometry()
            if not available.contains(frame):
                frame.moveCenter(available.center())
                self.move(frame.topLeft())

        def closeEvent(self, event) -> None:
            if (
                (self.smart.backup_thread and self.smart.backup_thread.isRunning())
                or (self.smart.preflight_thread and self.smart.preflight_thread.isRunning())
                or (self.full.full_backup_thread and self.full.full_backup_thread.isRunning())
                or (self.restore.restore_thread and self.restore.restore_thread.isRunning())
            ):
                MessageBox(t("backup_running_title"), t("backup_running_body"), self).exec()
                event.ignore()
                return
            self.smart.shutdown_workers()
            self.full.shutdown_workers()
            self.restore.shutdown_workers()
            settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            state = self.windowState()
            settings.window_state = str(state.value if hasattr(state, "value") else int(state))
            settings.current_page = self.smart.objectName()
            save_settings(settings)
            super().closeEvent(event)

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass

    app = QApplication(sys.argv)
    interaction_log_filter = InteractionLogFilter(app)
    app.installEventFilter(interaction_log_filter)
    app.installTranslator(FluentTranslator())
    apply_app_style(app)
    window = MainWindow()
    window.show()
    if auto_quit_ms is not None:
        QTimer.singleShot(auto_quit_ms, app.quit)
    return app.exec()
