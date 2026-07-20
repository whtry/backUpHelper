from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event

from backup.package import create_backup_package
from core.models import ArchiveFormat, BackupItem, BackupMode, RiskLevel


@dataclass(frozen=True)
class FullBackupTarget:
    path: Path
    label: str
    is_volume_root: bool = False


def _full_backup_item(target: FullBackupTarget, name: str, reason: str) -> BackupItem:
    return BackupItem(
        id="full-" + target.label.replace(":", "").replace("\\", "-").replace("/", "-"),
        name=name,
        category="Full Backup",
        path=target.path,
        reason=reason,
        risk=RiskLevel.SYSTEM if target.is_volume_root else RiskLevel.LARGE,
        default_selected=True,
        tags=("full-backup", "file-level"),
    )


def create_folder_backup(
    target: FullBackupTarget,
    destination: Path,
    archive_format: ArchiveFormat,
    encryption_password: str | None = None,
    output_path: Path | None = None,
    progress=None,
    temporary_root: Path | None = None,
    cancel_event: Event | None = None,
) -> Path:
    item = _full_backup_item(
        target,
        f"Folder backup: {target.label}",
        "File-level archive of the selected folder.",
    )
    return create_backup_package(
        destination=destination,
        selected_items=[item],
        archive_format=archive_format,
        mode=BackupMode.FILE_LEVEL_FULL,
        include_system_inventory=False,
        encryption_password=encryption_password,
        output_path=output_path,
        progress=progress,
        temporary_root=temporary_root,
        cancel_event=cancel_event,
    )


def create_volume_iso_backup(
    volume_root: Path,
    destination: Path,
    encryption_password: str | None = None,
    output_path: Path | None = None,
    progress=None,
    temporary_root: Path | None = None,
    cancel_event: Event | None = None,
) -> Path:
    target = FullBackupTarget(path=volume_root, label=str(volume_root), is_volume_root=True)
    item = _full_backup_item(
        target,
        f"Volume ISO backup: {target.label}",
        "File-level ISO archive of the whole selected volume.",
    )
    return create_backup_package(
        destination=destination,
        selected_items=[item],
        archive_format=ArchiveFormat.ISO,
        mode=BackupMode.FILE_LEVEL_FULL,
        include_system_inventory=False,
        encryption_password=encryption_password,
        output_path=output_path,
        progress=progress,
        temporary_root=temporary_root,
        cancel_event=cancel_event,
    )


def create_file_level_full_backup(
    target: FullBackupTarget,
    destination: Path,
    archive_format: ArchiveFormat,
    encryption_password: str | None = None,
    output_path: Path | None = None,
    progress=None,
    temporary_root: Path | None = None,
    cancel_event: Event | None = None,
) -> Path:
    if target.is_volume_root and archive_format == ArchiveFormat.ISO:
        return create_volume_iso_backup(
            target.path,
            destination,
            encryption_password,
            output_path,
            progress,
            temporary_root,
            cancel_event,
        )
    return create_folder_backup(
        target,
        destination,
        archive_format,
        encryption_password,
        output_path,
        progress,
        temporary_root,
        cancel_event,
    )
