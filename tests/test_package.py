from __future__ import annotations

from pathlib import Path

from backup.package import create_backup_package
from core.models import ArchiveFormat, BackupItem
from preview.file_preview import preview_entry_text
from preview.package_reader import read_manifest


def test_create_directory_package_with_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "settings.json").write_text('{"ok": true}', encoding="utf-8")
    item = BackupItem(
        id="demo",
        name="Demo App",
        category="Test",
        path=source,
        reason="Test application data.",
    )

    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.DIRECTORY,
        include_system_inventory=False,
    )
    manifest = read_manifest(package)

    assert manifest["schema_version"] == 1
    assert manifest["items"][0]["id"] == "demo"
    assert manifest["files"][0]["relative_path"] == "data/demo/settings.json"
    assert "ok" in preview_entry_text(package, "data/demo/settings.json")


def test_create_encrypted_zip_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "settings.json").write_text("{}", encoding="utf-8")
    item = BackupItem(
        id="demo",
        name="Demo App",
        category="Test",
        path=source,
        reason="Test application data.",
    )

    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.ZIP,
        include_system_inventory=False,
        encryption_password="secret",
    )

    assert package.name.endswith(".zip.enc")


def test_create_package_respects_item_exclusions(tmp_path: Path) -> None:
    source = tmp_path / "source"
    keep = source / "keep"
    drop = source / "drop"
    keep.mkdir(parents=True)
    drop.mkdir()
    (keep / "keep.txt").write_text("keep", encoding="utf-8")
    (drop / "drop.txt").write_text("drop", encoding="utf-8")
    item = BackupItem(
        id="demo",
        name="Demo App",
        category="Test",
        path=source,
        reason="Test selective folder backup.",
    )

    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.DIRECTORY,
        include_system_inventory=False,
        item_exclusions={"demo": {"drop"}},
    )
    manifest = read_manifest(package)
    paths = {file["relative_path"] for file in manifest["files"]}

    assert "data/demo/keep/keep.txt" in paths
    assert "data/demo/drop/drop.txt" not in paths
    assert manifest["metadata"]["item_exclusions"] == {"demo": ["drop"]}


def test_create_iso_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.txt").write_text("hello", encoding="utf-8")
    item = BackupItem(
        id="folder",
        name="Folder",
        category="Test",
        path=source,
        reason="ISO test.",
    )

    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.ISO,
        include_system_inventory=False,
    )

    assert package.suffix == ".iso"
    assert package.stat().st_size > 0
