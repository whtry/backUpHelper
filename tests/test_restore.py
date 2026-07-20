from __future__ import annotations

from pathlib import Path

from backup.package import create_backup_package
from core.models import ArchiveFormat, BackupItem
from restore.executor import execute_restore_plan
from restore.planner import build_restore_plan


def test_restore_plan_reports_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "profile.ini").write_text("x=1", encoding="utf-8")
    item = BackupItem(
        id="profile",
        name="Profile",
        category="Test",
        path=source,
        reason="Profile data.",
    )
    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.DIRECTORY,
        include_system_inventory=False,
    )
    restore_root = tmp_path / "restore"
    conflict = restore_root / "data" / "profile" / "profile.ini"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("x=old", encoding="utf-8")

    plan = build_restore_plan(package, restore_root)

    assert len(plan.operations) == 1
    assert plan.operations[0].conflict is True


def test_restore_executor_skips_then_overwrites_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "profile.ini").write_text("x=new", encoding="utf-8")
    item = BackupItem(
        id="profile",
        name="Profile",
        category="Test",
        path=source,
        reason="Profile data.",
    )
    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.ZIP,
        include_system_inventory=False,
    )
    restore_root = tmp_path / "restore"
    target = restore_root / "data" / "profile" / "profile.ini"
    target.parent.mkdir(parents=True)
    target.write_text("x=old", encoding="utf-8")
    plan = build_restore_plan(package, restore_root)

    skipped = execute_restore_plan(plan, "skip")
    assert skipped.restored == 0
    assert skipped.skipped_conflicts == 1
    assert target.read_text(encoding="utf-8") == "x=old"

    restored = execute_restore_plan(plan, "overwrite")
    assert restored.restored == 1
    assert target.read_text(encoding="utf-8") == "x=new"
