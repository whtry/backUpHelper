from __future__ import annotations

from pathlib import Path

from backup.package import create_backup_package
from core.models import ArchiveFormat, BackupItem
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
