from __future__ import annotations

from pathlib import Path
from threading import Event

import backup.package as package_module
from backup.package import create_backup_package
from core.cancellation import OperationCancelledError
from core.models import ArchiveFormat, BackupItem
from preview.file_preview import preview_entry_text
from preview.package_reader import copy_entry_to, extract_package_to, list_entries, read_manifest


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


def test_extract_zip_package_to_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "settings.json").write_text('{"ok": true}', encoding="utf-8")
    item = BackupItem(
        id="demo",
        name="Demo App",
        category="Test",
        path=source,
        reason="Extraction test.",
    )
    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.ZIP,
        include_system_inventory=False,
    )

    extraction = extract_package_to(package, tmp_path / "extracted")

    assert extraction == tmp_path / "extracted"
    extracted_file = extraction / "data" / "demo" / "settings.json"
    assert extracted_file.read_text(encoding="utf-8") == '{"ok": true}'


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


def test_zip_archive_reads_sources_directly_without_data_staging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "settings.json").write_text("{}", encoding="utf-8")
    item = BackupItem(
        id="demo",
        name="Demo App",
        category="Test",
        path=source,
        reason="Direct archive test.",
    )

    def fail_if_staged(*_args, **_kwargs):
        raise AssertionError("ZIP source data should not be staged before compression")

    monkeypatch.setattr(package_module, "_stage_files", fail_if_staged)
    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.ZIP,
        include_system_inventory=False,
    )

    assert package.is_file()
    assert read_manifest(package)["files"][0]["relative_path"] == "data/demo/settings.json"


def test_cancelled_archive_removes_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.json").write_text("{}", encoding="utf-8")
    (source / "second.json").write_text("{}", encoding="utf-8")
    item = BackupItem(
        id="demo",
        name="Demo App",
        category="Test",
        path=source,
        reason="Cancellation test.",
    )
    cancel_event = Event()
    output = tmp_path / "out" / "cancelled.zip"

    def stop_after_first_source_file(message: str, _current: int, _total: int) -> None:
        if message.startswith("Compressed data/demo/"):
            cancel_event.set()

    try:
        create_backup_package(
            output.parent,
            [item],
            ArchiveFormat.ZIP,
            include_system_inventory=False,
            output_path=output,
            cancel_event=cancel_event,
            progress=stop_after_first_source_file,
        )
    except OperationCancelledError:
        pass
    else:
        raise AssertionError("Expected cancelled backup to stop")

    assert not output.exists()
    assert not output.with_suffix(".zip.enc").exists()


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


def test_create_package_respects_explicit_item_inclusions(tmp_path: Path) -> None:
    source = tmp_path / "source"
    include = source / "include"
    exclude = source / "exclude"
    include.mkdir(parents=True)
    exclude.mkdir()
    (include / "keep.txt").write_text("keep", encoding="utf-8")
    (exclude / "skip.txt").write_text("skip", encoding="utf-8")
    item = BackupItem(
        id="demo",
        name="Demo App",
        category="Test",
        path=source,
        reason="Test partial folder backup.",
    )

    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.DIRECTORY,
        include_system_inventory=False,
        item_inclusions={"demo": {"include"}},
    )
    manifest = read_manifest(package)
    paths = {file["relative_path"] for file in manifest["files"]}

    assert "data/demo/include/keep.txt" in paths
    assert "data/demo/exclude/skip.txt" not in paths
    assert manifest["metadata"]["item_inclusions"] == {"demo": ["include"]}


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
    assert read_manifest(package)["items"][0]["id"] == "folder"
    assert "manifest.json" in {entry.path for entry in list_entries(package)}


def test_rejects_output_inside_selected_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.txt").write_text("hello", encoding="utf-8")
    item = BackupItem(
        id="folder",
        name="Folder",
        category="Test",
        path=source,
        reason="Output validation test.",
    )

    try:
        create_backup_package(
            source,
            [item],
            ArchiveFormat.ZIP,
            include_system_inventory=False,
            output_path=source / "backup.zip",
        )
    except ValueError as exc:
        assert "outside the selected source" in str(exc)
    else:
        raise AssertionError("Expected output path validation to fail")


def test_uses_configured_temporary_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.txt").write_text("hello", encoding="utf-8")
    work_root = tmp_path / "work"
    item = BackupItem(
        id="folder",
        name="Folder",
        category="Test",
        path=source,
        reason="Temporary root test.",
    )

    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.ZIP,
        include_system_inventory=False,
        temporary_root=work_root,
    )

    assert package.exists()
    assert work_root.is_dir()
    assert not list(work_root.glob("back-up-helper-*"))


def test_rejects_temporary_root_inside_selected_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    item = BackupItem(
        id="folder",
        name="Folder",
        category="Test",
        path=source,
        reason="Temporary root validation test.",
    )

    try:
        create_backup_package(
            tmp_path / "out",
            [item],
            ArchiveFormat.ZIP,
            include_system_inventory=False,
            temporary_root=source / "work",
        )
    except ValueError as exc:
        assert "Temporary root must be outside" in str(exc)
    else:
        raise AssertionError("Expected temporary root validation to fail")


def test_uses_configured_temporary_root_for_7z_read_and_restore(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.txt").write_text("hello", encoding="utf-8")
    work_root = tmp_path / "work"
    item = BackupItem(
        id="folder",
        name="Folder",
        category="Test",
        path=source,
        reason="7z temporary root test.",
    )

    package = create_backup_package(
        tmp_path / "out",
        [item],
        ArchiveFormat.SEVEN_Z,
        include_system_inventory=False,
        temporary_root=work_root,
    )
    manifest = read_manifest(package, work_root)
    restored = tmp_path / "restore" / "note.txt"
    copy_entry_to(package, "data/folder/note.txt", restored, work_root)

    assert manifest["items"][0]["id"] == "folder"
    assert restored.read_text(encoding="utf-8") == "hello"
