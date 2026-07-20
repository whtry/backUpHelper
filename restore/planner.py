from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from preview.package_reader import read_manifest


@dataclass(frozen=True)
class RestoreOperation:
    source_relative_path: str
    destination_path: Path
    conflict: bool
    source_item_id: str


@dataclass(frozen=True)
class RestorePlan:
    package_path: Path
    operations: list[RestoreOperation]
    registry_keys: list[str]
    sensitive_item_ids: list[str]


def build_restore_plan(
    package_path: Path,
    restore_root: Path,
    temporary_root: Path | None = None,
) -> RestorePlan:
    manifest = read_manifest(package_path, temporary_root)
    operations: list[RestoreOperation] = []
    for file_entry in manifest.get("files", []):
        source_relative = file_entry["relative_path"]
        destination = restore_root / source_relative
        operations.append(
            RestoreOperation(
                source_relative_path=source_relative,
                destination_path=destination,
                conflict=destination.exists(),
                source_item_id=file_entry["source_item_id"],
            )
        )

    registry_keys = []
    for item in manifest.get("items", []):
        registry_keys.extend(item.get("registry_keys", []))

    return RestorePlan(
        package_path=package_path,
        operations=operations,
        registry_keys=registry_keys,
        sensitive_item_ids=manifest.get("metadata", {}).get("sensitive_items", []),
    )
