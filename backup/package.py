from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

from backup.walker import iter_files
from core.cancellation import OperationCancelledError, raise_if_cancelled
from core.checksum import sha256_file
from core.encryption import encrypt_file
from core.models import ArchiveFormat, BackupItem, BackupManifest, BackupMode, ManifestFile
from core.serialization import dump_json
from core.temporary import prepare_temporary_root, temporary_directory
from inventory.conda import build_conda_export_plans, export_conda_environment_files
from inventory.icons import copy_application_icons
from inventory.installed_apps import list_installed_applications
from inventory.registry import export_registry_keys

ProgressCallback = Callable[[str, int, int], None]
_MEBIBYTE = 1024 * 1024


@dataclass(frozen=True)
class ArchiveSource:
    source_path: Path
    archive_path: str


@dataclass(frozen=True)
class BackupSpaceEstimate:
    source_bytes: int
    source_files: int
    output_peak_bytes: int
    temporary_peak_bytes: int


@dataclass(frozen=True)
class BackupSpaceCheck:
    estimate: BackupSpaceEstimate
    destination_free_bytes: int
    temporary_free_bytes: int
    destination_required_bytes: int
    temporary_required_bytes: int
    shared_volume: bool

    @property
    def is_sufficient(self) -> bool:
        if self.shared_volume:
            return self.destination_free_bytes >= self.destination_required_bytes
        return (
            self.destination_free_bytes >= self.destination_required_bytes
            and self.temporary_free_bytes >= self.temporary_required_bytes
        )


class InsufficientBackupSpaceError(RuntimeError):
    pass


def _safe_item_root(item: BackupItem) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in item.id)


def _emit(progress: ProgressCallback | None, message: str, current: int, total: int) -> None:
    if progress:
        progress(message, current, total)


def _scaled_progress(
    progress: ProgressCallback | None,
    start: int,
    end: int,
) -> ProgressCallback | None:
    if progress is None:
        return None

    def emit(message: str, current: int, total: int) -> None:
        fraction = current / total if total > 0 else 0
        _emit(progress, message, start + round((end - start) * fraction), 100)

    return emit


def _emit_archive_step(
    progress: ProgressCallback | None,
    message: str,
    index: int,
    total: int,
) -> None:
    """Limit queued UI updates while still reporting a useful archive percentage."""
    interval = max(1, total // 100)
    if index == 1 or index == total or index % interval == 0:
        _emit(progress, message, index, total)


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


def format_bytes(size: int) -> str:
    """Format a byte count for a concise preflight message."""
    value = float(max(0, size))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{int(value)} B"


def _nearest_existing_directory(path: Path) -> Path:
    candidate = path.expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else candidate.parent


def _volume_key(path: Path) -> str:
    return str(_nearest_existing_directory(path).resolve().anchor).casefold()


def estimate_backup_space(
    selected_items: list[BackupItem],
    archive_format: ArchiveFormat,
    encryption_enabled: bool = False,
    item_exclusions: dict[str, set[str]] | None = None,
    item_inclusions: dict[str, set[str]] | None = None,
    cancel_event: Event | None = None,
) -> BackupSpaceEstimate:
    """Estimate peak extra disk use without copying selected source data."""
    item_exclusions = item_exclusions or {}
    item_inclusions = item_inclusions or {}
    source_bytes = 0
    source_files = 0
    for item in selected_items:
        for source in iter_files(
            item.path,
            excluded_relative_paths=item_exclusions.get(item.id, set()),
            included_relative_paths=item_inclusions.get(item.id),
        ):
            raise_if_cancelled(cancel_event)
            try:
                source_bytes += source.stat().st_size
                source_files += 1
            except OSError:
                continue

    # Archives can be slightly larger than incompressible inputs. This reserve also
    # covers the manifest, registry export and extracted application icons.
    metadata_reserve = max(64 * _MEBIBYTE, source_files * 1024 + 8 * _MEBIBYTE)
    archive_reserve = source_bytes + max(8 * _MEBIBYTE, source_files * 512)
    if archive_format == ArchiveFormat.ISO:
        archive_reserve += 8 * _MEBIBYTE
    output_peak = archive_reserve * (2 if encryption_enabled else 1)
    temporary_peak = metadata_reserve
    if archive_format == ArchiveFormat.DIRECTORY:
        temporary_peak += archive_reserve

    return BackupSpaceEstimate(
        source_bytes=source_bytes,
        source_files=source_files,
        output_peak_bytes=output_peak,
        temporary_peak_bytes=temporary_peak,
    )


def check_backup_space(
    output_path: Path,
    temporary_root: Path | None,
    estimate: BackupSpaceEstimate,
) -> BackupSpaceCheck:
    """Check peak free space on output and temporary-work volumes."""
    destination_dir = _nearest_existing_directory(output_path.parent)
    temporary_dir = _nearest_existing_directory(temporary_root or Path(tempfile.gettempdir()))
    shared_volume = _volume_key(destination_dir) == _volume_key(temporary_dir)
    destination_required = estimate.output_peak_bytes
    temporary_required = estimate.temporary_peak_bytes
    if shared_volume:
        destination_required += temporary_required

    return BackupSpaceCheck(
        estimate=estimate,
        destination_free_bytes=shutil.disk_usage(destination_dir).free,
        temporary_free_bytes=shutil.disk_usage(temporary_dir).free,
        destination_required_bytes=destination_required,
        temporary_required_bytes=temporary_required,
        shared_volume=shared_volume,
    )


def ensure_backup_space(
    output_path: Path,
    temporary_root: Path | None,
    estimate: BackupSpaceEstimate,
) -> BackupSpaceCheck:
    check = check_backup_space(output_path, temporary_root, estimate)
    if check.is_sufficient:
        return check
    if check.shared_volume:
        raise InsufficientBackupSpaceError(
            "Insufficient free space on the output/work volume: "
            f"requires {format_bytes(check.destination_required_bytes)}, "
            f"available {format_bytes(check.destination_free_bytes)}."
        )
    raise InsufficientBackupSpaceError(
        "Insufficient free space: "
        f"output requires {format_bytes(check.destination_required_bytes)} "
        f"(available {format_bytes(check.destination_free_bytes)}); "
        f"temporary work requires {format_bytes(check.temporary_required_bytes)} "
        f"(available {format_bytes(check.temporary_free_bytes)})."
    )


def _stage_files(
    stage_dir: Path,
    selected_items: list[BackupItem],
    item_exclusions: dict[str, set[str]] | None = None,
    item_inclusions: dict[str, set[str]] | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> list[ManifestFile]:
    files: list[ManifestFile] = []
    data_dir = stage_dir / "data"
    item_exclusions = item_exclusions or {}
    item_inclusions = item_inclusions or {}
    total = max(1, len(selected_items))
    for item_index, item in enumerate(selected_items, start=1):
        raise_if_cancelled(cancel_event)
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
            raise_if_cancelled(cancel_event)
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


def _collect_archive_sources(
    selected_items: list[BackupItem],
    item_exclusions: dict[str, set[str]] | None = None,
    item_inclusions: dict[str, set[str]] | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> tuple[list[ManifestFile], list[ArchiveSource]]:
    """Index source files without duplicating them into a staging directory."""
    files: list[ManifestFile] = []
    sources: list[ArchiveSource] = []
    item_exclusions = item_exclusions or {}
    item_inclusions = item_inclusions or {}
    total = max(1, len(selected_items))
    for item_index, item in enumerate(selected_items, start=1):
        raise_if_cancelled(cancel_event)
        _emit(progress, f"Indexing {item.name}: {item.path}", item_index - 1, total)
        if not item.path.exists():
            continue
        item_root = Path("data") / _safe_item_root(item)
        source_base = item.path if item.path.is_dir() else item.path.parent
        for source in iter_files(
            item.path,
            excluded_relative_paths=item_exclusions.get(item.id, set()),
            included_relative_paths=item_inclusions.get(item.id),
        ):
            raise_if_cancelled(cancel_event)
            try:
                relative = (item_root / source.relative_to(source_base)).as_posix()
                size = source.stat().st_size
                checksum = sha256_file(source)
            except OSError:
                continue
            files.append(
                ManifestFile(
                    relative_path=relative,
                    size=size,
                    sha256=checksum,
                    source_item_id=item.id,
                )
            )
            sources.append(ArchiveSource(source, relative))
        _emit(progress, f"Indexed {item.name}", item_index, total)
    return files, sources


def _write_manifest(
    metadata_dir: Path,
    selected_items: list[BackupItem],
    files: list[ManifestFile],
    applications: list,
    mode: BackupMode,
    archive_format: ArchiveFormat,
    item_exclusions: dict[str, set[str]] | None,
    item_inclusions: dict[str, set[str]] | None,
) -> None:
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
    dump_json(metadata_dir / "manifest.json", manifest)


def _prepare_direct_archive_sources(
    metadata_dir: Path,
    selected_items: list[BackupItem],
    mode: BackupMode,
    archive_format: ArchiveFormat,
    include_system_inventory: bool,
    selected_application_names: set[str] | None,
    item_exclusions: dict[str, set[str]] | None,
    item_inclusions: dict[str, set[str]] | None,
    progress: ProgressCallback | None,
    cancel_event: Event | None = None,
) -> list[ArchiveSource]:
    files, sources = _collect_archive_sources(
        selected_items,
        item_exclusions,
        item_inclusions,
        progress,
        cancel_event,
    )
    raise_if_cancelled(cancel_event)
    applications = _write_supporting_inventory(
        metadata_dir,
        include_system_inventory,
        selected_application_names,
        progress,
        cancel_event,
    )
    _emit(progress, "Writing manifest", 0, 1)
    raise_if_cancelled(cancel_event)
    _write_manifest(
        metadata_dir,
        selected_items,
        files,
        applications,
        mode,
        archive_format,
        item_exclusions,
        item_inclusions,
    )
    _emit(progress, "Manifest written", 1, 1)
    for path in metadata_dir.rglob("*"):
        raise_if_cancelled(cancel_event)
        if path.is_file():
            sources.append(ArchiveSource(path, path.relative_to(metadata_dir).as_posix()))
    return sources


def _write_supporting_inventory(
    stage_dir: Path,
    include_system_inventory: bool,
    selected_application_names: set[str] | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> list:
    raise_if_cancelled(cancel_event)
    _emit(progress, "Writing software inventory", 1, 4)
    applications = list_installed_applications() if include_system_inventory else []
    if selected_application_names is not None:
        applications = [app for app in applications if app.name in selected_application_names]
    dump_json(stage_dir / "inventory" / "installed_applications.json", applications)
    icon_index = copy_application_icons(applications, stage_dir / "inventory" / "icons")
    dump_json(stage_dir / "inventory" / "application_icons.json", icon_index)
    raise_if_cancelled(cancel_event)
    _emit(progress, "Writing Conda export plan", 2, 4)
    conda_plans = build_conda_export_plans() if include_system_inventory else []
    dump_json(stage_dir / "inventory" / "conda_export_plans.json", conda_plans)
    conda_exports = export_conda_environment_files(
        stage_dir / "inventory" / "conda",
        conda_plans,
        cancel_event,
    )
    dump_json(stage_dir / "inventory" / "conda_export_results.json", conda_exports)
    registry_keys = [app.registry_key for app in applications if app.registry_key]
    registry_results = (
        export_registry_keys(registry_keys, stage_dir / "registry" / "installed_apps")
        if include_system_inventory
        else []
    )
    _emit(progress, "Writing restore notes", 3, 4)
    raise_if_cancelled(cancel_event)
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
    cancel_event: Event | None = None,
) -> Path | None:
    executable = _find_7z()
    if not executable:
        return None
    _emit(progress, f"Compressing with 7-Zip: {archive_path.name}", -1, 0)
    command = [
        executable,
        "a",
        f"-t{archive_type}",
        "-mmt=on",
        "-mx=5",
        "-bsp1",
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
        output: Queue[str | None] = Queue()

        def read_output() -> None:
            assert process.stdout
            for character in iter(lambda: process.stdout.read(1), ""):
                output.put(character)
            output.put(None)

        Thread(target=read_output, daemon=True).start()
        buffer = ""
        while True:
            if cancel_event and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                raise OperationCancelledError("Operation cancelled while 7-Zip was compressing")
            try:
                character = output.get(timeout=0.1)
            except Empty:
                continue
            if character is None:
                break
            if character not in "\r\n":
                buffer += character
                continue
            line = buffer.strip()
            buffer = ""
            if not line:
                continue
            match = re.search(r"(?<!\d)(\d{1,3})%", line)
            if match:
                _emit(progress, line, min(100, int(match.group(1))), 100)
            else:
                _emit(progress, line, -1, 0)
        if buffer.strip():
            _emit(progress, buffer.strip(), -1, 0)
        if process.wait() != 0:
            raise RuntimeError(f"7-Zip failed with exit code {process.returncode}")
        _emit(progress, f"Compressed {archive_path.name}", 1, 1)
        return archive_path
    finally:
        if seven_zip_work_root:
            shutil.rmtree(seven_zip_work_root, ignore_errors=True)


def _zip_sources(
    sources: list[ArchiveSource],
    archive_path: Path,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> Path:
    total = max(1, len(sources))
    _emit(progress, f"Compressing zip: {archive_path.name}", 0, total)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, entry in enumerate(sources, start=1):
            raise_if_cancelled(cancel_event)
            archive.write(entry.source_path, entry.archive_path)
            _emit_archive_step(progress, f"Compressed {entry.archive_path}", index, total)
    return archive_path


def _seven_z_sources(
    sources: list[ArchiveSource],
    archive_path: Path,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> Path:
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("py7zr is required to create .7z packages") from exc

    total = max(1, len(sources))
    _emit(progress, f"Compressing 7z: {archive_path.name}", 0, total)
    with py7zr.SevenZipFile(archive_path, "w", mp=True) as archive:
        for index, entry in enumerate(sources, start=1):
            raise_if_cancelled(cancel_event)
            archive.write(entry.source_path, arcname=entry.archive_path)
            _emit_archive_step(progress, f"Compressed {entry.archive_path}", index, total)
    return archive_path


def _iso_sources(
    sources: list[ArchiveSource],
    archive_path: Path,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> Path:
    try:
        import pycdlib
    except ImportError as exc:
        raise RuntimeError("pycdlib is required to create ISO packages") from exc

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=True, rock_ridge="1.09")
    directories: set[tuple[str, ...]] = set()
    total = max(1, len(sources))
    try:
        _emit(progress, f"Writing ISO: {archive_path.name}", 0, total)
        for index, entry in enumerate(sources, start=1):
            raise_if_cancelled(cancel_event)
            relative = Path(entry.archive_path)
            for depth in range(1, len(relative.parts)):
                parts = relative.parts[:depth]
                if parts in directories:
                    continue
                directories.add(parts)
                iso_path = "/" + "/".join(part.upper()[:31] for part in parts)
                joliet_path = "/" + "/".join(parts)
                iso.add_directory(
                    iso_path=iso_path,
                    rr_name=parts[-1],
                    joliet_path=joliet_path,
                )
            iso_path = "/" + "/".join(part.upper()[:31] for part in relative.parts) + ";1"
            iso.add_file(
                str(entry.source_path),
                iso_path=iso_path,
                rr_name=relative.name,
                joliet_path="/" + relative.as_posix(),
            )
            _emit_archive_step(progress, f"Wrote {entry.archive_path}", index, total)
        iso.write(str(archive_path))
        _emit(progress, f"Wrote ISO {archive_path.name}", total, total)
    finally:
        iso.close()
    return archive_path


def _zip_directory(
    stage_dir: Path,
    archive_path: Path,
    progress: ProgressCallback | None = None,
    temporary_root: Path | None = None,
    cancel_event: Event | None = None,
) -> Path:
    seven_zip = _archive_with_7z(
        stage_dir,
        archive_path,
        "zip",
        progress,
        temporary_root,
        cancel_event,
    )
    if seven_zip:
        return seven_zip
    _emit(progress, f"Compressing zip: {archive_path.name}", 0, 1)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in stage_dir.rglob("*"):
            raise_if_cancelled(cancel_event)
            if path.is_file():
                archive.write(path, path.relative_to(stage_dir).as_posix())
    _emit(progress, f"Compressed {archive_path.name}", 1, 1)
    return archive_path


def _seven_z_directory(
    stage_dir: Path,
    archive_path: Path,
    progress: ProgressCallback | None = None,
    temporary_root: Path | None = None,
    cancel_event: Event | None = None,
) -> Path:
    seven_zip = _archive_with_7z(
        stage_dir,
        archive_path,
        "7z",
        progress,
        temporary_root,
        cancel_event,
    )
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
    cancel_event: Event | None = None,
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
        cancel_event,
    )
    applications = _write_supporting_inventory(
        stage_dir,
        include_system_inventory,
        selected_application_names,
        progress,
        cancel_event,
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


def _create_backup_package(
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
    space_estimate: BackupSpaceEstimate | None = None,
    cancel_event: Event | None = None,
) -> Path:
    proposed_output = output_path or destination / "backUpHelper-package"
    temp_root = prepare_temporary_root(temporary_root)
    validate_backup_destination(selected_items, proposed_output, temp_root)
    destination.mkdir(parents=True, exist_ok=True)
    _emit(progress, "Starting backup package", 0, 5)

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if archive_format == ArchiveFormat.DIRECTORY:
        expected_output = output_path or destination / f"backUpHelper-{timestamp}"
    else:
        suffix = {
            ArchiveFormat.ZIP: ".zip",
            ArchiveFormat.SEVEN_Z: ".7z",
            ArchiveFormat.ISO: ".iso",
        }[archive_format]
        expected_output = _archive_path_for(
            output_path,
            destination,
            f"backUpHelper-{timestamp}",
            suffix,
        )
    estimate = space_estimate or estimate_backup_space(
        selected_items,
        archive_format,
        bool(encryption_password),
        item_exclusions,
        item_inclusions,
        cancel_event,
    )
    ensure_backup_space(expected_output, temp_root, estimate)

    if archive_format != ArchiveFormat.DIRECTORY:
        with temporary_directory(
            prefix="back-up-helper-metadata-",
            temporary_root=temp_root,
        ) as temp:
            metadata_dir = Path(temp) / "package"
            metadata_dir.mkdir()
            sources = _prepare_direct_archive_sources(
                metadata_dir,
                selected_items,
                mode,
                archive_format,
                include_system_inventory,
                selected_application_names,
                item_exclusions,
                item_inclusions,
                _scaled_progress(progress, 0, 35),
                cancel_event,
            )
            if archive_format == ArchiveFormat.ZIP:
                package = _zip_sources(
                    sources,
                    expected_output,
                    _scaled_progress(progress, 35, 99),
                    cancel_event,
                )
            elif archive_format == ArchiveFormat.SEVEN_Z:
                package = _seven_z_sources(
                    sources,
                    expected_output,
                    _scaled_progress(progress, 35, 99),
                    cancel_event,
                )
            elif archive_format == ArchiveFormat.ISO:
                package = _iso_sources(
                    sources,
                    expected_output,
                    _scaled_progress(progress, 35, 99),
                    cancel_event,
                )
            else:
                raise ValueError(f"Unsupported archive format: {archive_format}")
            return _encrypt_if_needed(
                package,
                encryption_password,
                _scaled_progress(progress, 99, 100),
            )

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
            cancel_event=cancel_event,
        )
        if encryption_password:
            zipped = _zip_directory(
                stage_dir,
                _archive_path_for(output_path, destination, stage_dir.name, ".zip"),
                progress,
                temp_root,
                cancel_event,
            )
            return _encrypt_if_needed(zipped, encryption_password, progress)
        final_dir = expected_output
        shutil.copytree(stage_dir, final_dir)
        _emit(progress, f"Created directory package: {final_dir}", 5, 5)
        return final_dir


def _remove_partial_output(output_path: Path) -> None:
    candidates = [output_path, output_path.with_suffix(output_path.suffix + ".enc")]
    for candidate in candidates:
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)
            else:
                candidate.unlink(missing_ok=True)
        except OSError:
            continue


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
    space_estimate: BackupSpaceEstimate | None = None,
    cancel_event: Event | None = None,
) -> Path:
    expected_output = output_path or destination / "backUpHelper-package"
    try:
        return _create_backup_package(
            destination,
            selected_items,
            archive_format,
            mode,
            include_system_inventory,
            encryption_password,
            selected_application_names,
            item_exclusions,
            item_inclusions,
            output_path,
            progress,
            temporary_root,
            space_estimate,
            cancel_event,
        )
    except OperationCancelledError:
        _remove_partial_output(expected_output)
        _emit(
            progress,
            "Operation cancelled. Removed incomplete output and temporary files.",
            0,
            100,
        )
        raise
