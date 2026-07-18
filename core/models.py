from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class RiskLevel(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    LARGE = "large"
    SYSTEM = "system"


class ArchiveFormat(StrEnum):
    ZIP = "zip"
    SEVEN_Z = "7z"
    ISO = "iso"
    DIRECTORY = "directory"


class BackupMode(StrEnum):
    SMART = "smart"
    FILE_LEVEL_FULL = "file_level_full"
    DISK_IMAGE = "disk_image"


@dataclass(frozen=True)
class BackupSource:
    path: Path
    reason: str
    optional: bool = True
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackupItem:
    id: str
    name: str
    category: str
    path: Path
    reason: str
    software: str | None = None
    icon_path: Path | None = None
    risk: RiskLevel = RiskLevel.NORMAL
    sensitive: bool = False
    default_selected: bool = True
    tags: tuple[str, ...] = ()
    registry_keys: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def exists(self) -> bool:
        return self.path.exists()


@dataclass(frozen=True)
class InstalledApplication:
    name: str
    version: str | None = None
    publisher: str | None = None
    install_location: str | None = None
    uninstall_string: str | None = None
    icon_path: str | None = None
    registry_key: str | None = None


@dataclass(frozen=True)
class ManifestFile:
    relative_path: str
    size: int
    sha256: str
    source_item_id: str


@dataclass(frozen=True)
class BackupManifest:
    schema_version: int
    created_at: str
    mode: BackupMode
    archive_format: ArchiveFormat
    items: list[BackupItem]
    files: list[ManifestFile]
    installed_applications: list[InstalledApplication]
    metadata: dict[str, Any] = field(default_factory=dict)
