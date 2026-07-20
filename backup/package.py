from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from backup.walker import iter_files
from core.checksum import sha256_file
from core.encryption import encrypt_file
from core.models import ArchiveFormat, BackupItem, BackupManifest, BackupMode, ManifestFile
from core.serialization import dump_json
from core.temporary import prepare_temporary_root, temporary_directory
from inventory.conda import build_conda_export_plans
from inventory.icons import copy_application_icons
from inventory.installed_apps import list_installed_applications
from inventory.registry import export_registry_keys

ProgressCallback = Callable[[str, int, int], None]


def _safe_item_root(item: BackupItem) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in item.id)


def _emit(progress: ProgressCallback | None, message: str, current: int, total: int) -> None:
    if progress:
        progress(message, current, total)


def _archive_path_for(output_path: Path | None, destination: Path, name: str, suffix: str) -> Path:
    if output_path:
        return output_path.with_suffix(suffix)
    return (destination / name).with_suffix(suffix)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def validate_backup_destination(
    selected_items: list[BackupItem],
    output_path: Path,
    temporary_root: Path | None = None,
) -> None:
    """Reject output paths that would be captured by the source selection itself."""
    for item in selected_items:
        if item.path.is_dir() and _is_within(output_path, item.path):
            raise ValueError(
                f"Backup output must be outside the selected source: {item.path} -> {output_path}"
            )
        if item.path.is_file() and output_path.resolve() == item.path.resolve():
            raise ValueError(f"Backup output cannot replace the selected source: {item.path}")
        if temporary_root and item.path.is_dir() and _is_within(temporary_root, item.path):
            raise ValueError(
                "Temporary root must be outside the selected source: "
                f"{item.path} -> {temporary_root}"
            )


def _stage_files(
    stage_dir: Path,
    selected_items: list[BackupItem],
    item_exclusions: dict[str, set[str]] | None = None,
    item_inclusions: dict[str, set[str]] | None = None,
    progress: ProgressCallback | None = None,
) -> list[ManifestFile]:
    files: list[ManifestFile] = []
    data_dir = stage_dir / "data"
    item_exclusions = item_exclusions or {}
    item_inclusions = item_inclusions or {}
    total = max(1, len(selected_items))
    for item_index, item in enumerate(selected_items, start=1):
        _emit(progress, f"Scanning {item.name}: {item.path}", item_index - 1, total)
        if not item.path.exists():
            continue
        item_root = data_dir / _safe_item_root(item)
        source_base = item.path if item.path.is_dir() else item.path.parent
        for source in iter_files(
            item.path,
            excluded_relative_paths=item_exclusions.get(item.id, set()),
            included_relative_paths=item_inclusions.get(item.id),
        ):
            rel_from_source = source.relative_to(source_base)
            target = item_root / rel_from_source
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
            except OSError:
                continue
            relative = target.relative_to(stage_dir).as_posix()
            files.append(
                ManifestFile(
                    relative_path=relative,
                    size=target.stat().st_size,
                    sha256=sha256_file(target),
                    source_item_id=item.id,
                )
            )
        _emit(progress, f"Copied {item.name}", item_index, total)
    return files


def _write_supporting_inventory(
    stage_dir: Path,
    include_system_inventory: bool,
    selected_application_names: set[str] | None = None,
    progress: ProgressCallback | None = None,
) -> list:
    _emit(progress, "Writing software inventory", 1, 4)
    applications = list_installed_applications() if include_system_inventory else []
    if selected_application_names is not None:
        applications = [app for app in applications if app.name in selected_application_names]
    dump_json(stage_dir / "inventory" / "installed_applications.json", applications)
    icon_index = copy_application_icons(applications, stage_dir / "inventory" / "icons")
    dump_json(stage_dir / "inventory" / "application_icons.json", icon_index)
    _emit(progress, "Writing Conda export plan", 2, 4)
    conda_plans = build_conda_export_plans() if include_system_inventory else []
    dump_json(stage_dir / "inventory" / "conda_export_plans.json", conda_plans)
    registry_keys = [app.registry_key for app in applications if app.registry_key]
    registry_results = (
        export_registry_keys(registry_keys, stage_dir / "registry" / "installed_apps")
        if include_system_inventory
        else []
    )
    _emit(progress, "Writing restore notes", 3, 4)
    dump_json(stage_dir / "registry" / "export_results.json", registry_results)
    (stage_dir / "RESTORE.md").write_text(
        "# Restore notes\n\n"
        "Review manifest.json before restoring files. Sensitive data should be restored only "
        "on a trusted machine. Conda commands are recorded for review and are not executed "
        "automatically.\n",
        encoding="utf-8",
    )
    _emit(progress, "Inventory complete", 4, 4)
    return applications


def _bundled_7z_candidates() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return [
        root / "tools" / "7zip" / "7zz.exe",
        root / "tools" / "7zip" / "7za.exe",
        root / "tools" / "7zip" / "7z.exe",
    ]


def _find_7z() -> str | None:
    for candidate in _bundled_7z_candidates():
        if candidate.exists():
            return str(candidate)
    return (
        shutil.which("7zz")
        or shutil.which("7za")
        or shutil.which("7z")
        or shutil.which("7z.exe")
    )


def _archive_with_7z(
    stage_dir: Path,
    archive_path: Path,
    archive_type: str,
    progress: ProgressCallback | None = None,
    temporary_root: Path | None = None,
) -> Path | None:
    executable = _find_7z()
    if not executable:
        return None
    _emit(progress, f"Compressing with 7-Zip: {archive_path.name}", 0, 1)
    command = [
        executable,
        "a",
        f"-t{archive_type}",
        "-mmt=on",
        "-mx=5",
        str(archive_path),
        ".",
    ]
    seven_zip_work_root: Path | None = None
    if temporary_root:
        seven_zip_work_root = Path(
            tempfile.mkdtemp(
                prefix="back-up-helper-7zip-",
                dir=prepare_temporary_root(temporary_root),
            )
        )
        command.insert(5, f"-w{seven_zip_work_root}")
    try:
        process = subprocess.Popen(
            command,
            cwd=stage_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        if seven_zip_work_root:
            shutil.rmtree(seven_zip_work_root, ignore_errors=True)
        _emit(progress, f"7-Zip unavailable, falling back: {exc}", 0, 1)
        return None
    try:
        assert process.stdout
        for line in process.stdout:
            line = line.strip()
            if line:
                _emit(progress, line, 0, 1)
        if process.wait() != 0:
            raise RuntimeError(f"7-Zip failed with exit code {process.returncode}")
        _emit(progress, f"Compressed {archive_path.name}", 1, 1)
        return archive_path
    finally:
        if seven_zip_work_root:
            shutil.rmtree(seven_zip_work_root, ignore_errors=True)


def _zip_directory(
    stage_dir: Path,
    archive_path: Path,
    progress: ProgressCallback | None = None,
    temporary_root: Path | None = None,
) -> Path:
    seven_zip = _archive_with_7z(stage_dir, archive_path, "zip", progress, temporary_root)
    if seven_zip:
        return seven_zip
    _emit(progress, f"Compressing zip: {archive_path.name}", 0, 1)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in stage_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(stage_dir).as_posix())
    _emit(progress, f"Compressed {archive_path.name}", 1, 1)
    return archive_path


def _seven_z_directory(
    stage_dir: Path,
    archive_path: Path,
    progress: ProgressCallback | None = None,
    temporary_root: Path | None = None,
) -> Path:
    seven_zip = _archive_with_7z(stage_dir, archive_path, "7z", progress, temporary_root)
    if seven_zip:
        return seven_zip
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("py7zr is required to create .7z packages") from exc

    _emit(progress, f"Compressing 7z with py7zr: {archive_path.name}", 0, 1)
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        archive.writeall(stage_dir, arcname=".")
    _emit(progress, f"Compressed {archive_path.name}", 1, 1)
    return archive_path


def _iso_directory(
    stage_dir: Path,
    archive_path: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    try:
        import pycdlib
    except ImportError as exc:
        raise RuntimeError("pycdlib is required to create ISO packages") from exc

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=True, rock_ridge="1.09")
    try:
        _emit(progress, f"Writing ISO: {archive_path.name}", 0, 1)
        for path in stage_dir.rglob("*"):
            relative = path.relative_to(stage_dir)
            iso_path = "/" + "/".join(part.upper()[:31] for part in relative.parts)
            rr_name = relative.name
            joliet_path = "/" + relative.as_posix()
            if path.is_dir():
                if relative.parts:
                    iso.add_directory(iso_path=iso_path, rr_name=rr_name, joliet_path=joliet_path)
            else:
                iso.add_file(
                    str(path),
                    iso_path=iso_path + ";1",
                    rr_name=rr_name,
                    joliet_path=joliet_path,
                )
        iso.write(str(archive_path))
        _emit(progress, f"Wrote ISO {archive_path.name}", 1, 1)
    finally:
        iso.close()
    return archive_path


def _encrypt_if_needed(
    package: Path,
    encryption_password: str | None,
    progress: ProgressCallback | None = None,
) -> Path:
    if not encryption_password:
        return package
    _emit(progress, f"Encrypting {package.name}", 0, 1)
    encrypted = encrypt_file(package, encryption_password).encrypted_path
    _emit(progress, f"Encrypted {encrypted.name}", 1, 1)
    return encrypted


def create_stage_directory(
    destination: Path,
    selected_items: list[BackupItem],
    mode: BackupMode = BackupMode.SMART,
    archive_format: ArchiveFormat = ArchiveFormat.DIRECTORY,
    include_system_inventory: bool = True,
    selected_application_names: set[str] | None = None,
    item_exclusions: dict[str, set[str]] | None = None,
    item_inclusions: dict[str, set[str]] | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    stage_dir = destination / f"backUpHelper-{timestamp}"
    stage_dir.mkdir(parents=True, exist_ok=False)
    files = _stage_files(
        stage_dir,
        selected_items,
        item_exclusions,
        item_inclusions,
        progress,
    )
    applications = _write_supporting_inventory(
        stage_dir,
        include_system_inventory,
        selected_application_names,
        progress,
    )
    _emit(progress, "Writing manifest", 0, 1)
    manifest = BackupManifest(
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        mode=mode,
        archive_format=archive_format,
        items=selected_items,
        files=files,
        installed_applications=applications,
        metadata={
            "sensitive_items": [item.id for item in selected_items if item.sensitive],
            "item_exclusions": {
                item_id: sorted(paths)
                for item_id, paths in (item_exclusions or {}).items()
                if paths
            },
            "item_inclusions": {
                item_id: sorted(paths)
                for item_id, paths in (item_inclusions or {}).items()
                if paths
            },
            "note": "File-level packages are not bootable disk images.",
        },
    )
    dump_json(stage_dir / "manifest.json", manifest)
    _emit(progress, "Manifest written", 1, 1)
    return stage_dir


def create_backup_package(
    destination: Path,
    selected_items: list[BackupItem],
    archive_format: ArchiveFormat = ArchiveFormat.ZIP,
    mode: BackupMode = BackupMode.SMART,
    include_system_inventory: bool = True,
    encryption_password: str | None = None,
    selected_application_names: set[str] | None = None,
    item_exclusions: dict[str, set[str]] | None = None,
    item_inclusions: dict[str, set[str]] | None = None,
    output_path: Path | None = None,
    progress: ProgressCallback | None = None,
    temporary_root: Path | None = None,
) -> Path:
    proposed_output = output_path or destination / "backUpHelper-package"
    temp_root = prepare_temporary_root(temporary_root)
    validate_backup_destination(selected_items, proposed_output, temp_root)
    destination.mkdir(parents=True, exist_ok=True)
    _emit(progress, "Starting backup package", 0, 5)
    with temporary_directory(prefix="back-up-helper-", temporary_root=temp_root) as temp:
        stage_parent = Path(temp)
        stage_dir = create_stage_directory(
            stage_parent,
            selected_items,
            mode=mode,
            archive_format=archive_format,
            include_system_inventory=include_system_inventory,
            selected_application_names=selected_application_names,
            item_exclusions=item_exclusions,
            item_inclusions=item_inclusions,
            progress=progress,
        )
        if archive_format == ArchiveFormat.DIRECTORY:
            if encryption_password:
                zipped = _zip_directory(
                    stage_dir,
                    _archive_path_for(output_path, destination, stage_dir.name, ".zip"),
                    progress,
                    temp_root,
                )
                return _encrypt_if_needed(zipped, encryption_password, progress)
            final_dir = output_path or destination / stage_dir.name
            shutil.copytree(stage_dir, final_dir)
            _emit(progress, f"Created directory package: {final_dir}", 5, 5)
            return final_dir
        if archive_format == ArchiveFormat.ZIP:
            package = _zip_directory(
                stage_dir,
                _archive_path_for(output_path, destination, stage_dir.name, ".zip"),
                progress,
                temp_root,
            )
            return _encrypt_if_needed(package, encryption_password, progress)
        if archive_format == ArchiveFormat.SEVEN_Z:
            package = _seven_z_directory(
                stage_dir,
                _archive_path_for(output_path, destination, stage_dir.name, ".7z"),
                progress,
                temp_root,
            )
            return _encrypt_if_needed(package, encryption_password, progress)
        if archive_format == ArchiveFormat.ISO:
            package = _iso_directory(
                stage_dir,
                _archive_path_for(output_path, destination, stage_dir.name, ".iso"),
                progress,
            )
            return _encrypt_if_needed(package, encryption_password, progress)
        raise ValueError(f"Unsupported archive format: {archive_format}")
