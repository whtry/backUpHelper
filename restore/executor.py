from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from preview.package_reader import copy_entry_to, read_manifest
from restore.planner import RestorePlan

ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class RestoreResult:
    restored: int
    skipped_conflicts: int
    skipped_unsafe: int


def _safe_relative_path(value: str) -> Path | None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return Path(*path.parts)


def execute_restore_plan(
    plan: RestorePlan,
    conflict_policy: str = "skip",
    progress: ProgressCallback | None = None,
    temporary_root: Path | None = None,
) -> RestoreResult:
    """Restore manifest files only, never registry data or executable commands."""
    if conflict_policy not in {"skip", "overwrite"}:
        raise ValueError("conflict_policy must be 'skip' or 'overwrite'")

    manifest = read_manifest(plan.package_path, temporary_root)
    manifest_files = {entry["relative_path"] for entry in manifest.get("files", [])}
    restored = skipped_conflicts = skipped_unsafe = 0
    total = max(1, len(plan.operations))
    for index, operation in enumerate(plan.operations, start=1):
        relative_path = _safe_relative_path(operation.source_relative_path)
        if relative_path is None or operation.source_relative_path not in manifest_files:
            skipped_unsafe += 1
            if progress:
                progress(f"Skipped unsafe entry: {operation.source_relative_path}", index, total)
            continue
        destination = operation.destination_path
        if destination.exists() and conflict_policy == "skip":
            skipped_conflicts += 1
            if progress:
                progress(f"Skipped existing file: {destination}", index, total)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_entry_to(
            plan.package_path,
            operation.source_relative_path,
            destination,
            temporary_root,
        )
        restored += 1
        if progress:
            progress(f"Restored {operation.source_relative_path}", index, total)
    return RestoreResult(restored, skipped_conflicts, skipped_unsafe)
