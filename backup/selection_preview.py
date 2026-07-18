from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backup.walker import iter_files
from core.models import BackupItem


@dataclass(frozen=True)
class SelectionPreviewFile:
    item_id: str
    item_name: str
    path: Path
    size: int


def preview_item_files(
    item: BackupItem,
    limit: int = 500,
    excluded_relative_paths: set[str] | None = None,
) -> list[SelectionPreviewFile]:
    files: list[SelectionPreviewFile] = []
    for path in iter_files(item.path, excluded_relative_paths=excluded_relative_paths or set()):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append(
            SelectionPreviewFile(
                item_id=item.id,
                item_name=item.name,
                path=path,
                size=size,
            )
        )
        if len(files) >= limit:
            break
    return files
