from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath

from core.serialization import load_json
from core.temporary import temporary_directory


@dataclass(frozen=True)
class PackageEntry:
    path: str
    size: int


def read_manifest(package_path: Path, temporary_root: Path | None = None) -> dict:
    if package_path.is_dir():
        return load_json(package_path / "manifest.json")
    if package_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(package_path) as archive:
            with archive.open("manifest.json") as handle:
                import json

                return json.loads(handle.read().decode("utf-8"))
    if package_path.suffix.lower() == ".7z":
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("py7zr is required to preview .7z packages") from exc

        with temporary_directory("back-up-helper-preview-", temporary_root) as temp:
            temp_dir = Path(temp)
            with py7zr.SevenZipFile(package_path) as archive:
                archive.extract(path=temp_dir, targets=["manifest.json"])
            import json

            return json.loads((temp_dir / "manifest.json").read_text(encoding="utf-8"))
    if package_path.suffix.lower() == ".iso":
        try:
            import pycdlib
        except ImportError as exc:
            raise RuntimeError("pycdlib is required to preview ISO packages") from exc

        iso = pycdlib.PyCdlib()
        try:
            iso.open(str(package_path))
            payload = BytesIO()
            iso.get_file_from_iso_fp(payload, joliet_path="/manifest.json")
            import json

            return json.loads(payload.getvalue().decode("utf-8"))
        finally:
            iso.close()
    raise RuntimeError(f"Manifest preview is not implemented for {package_path.suffix}")


def list_entries(package_path: Path) -> list[PackageEntry]:
    if package_path.is_dir():
        return [
            PackageEntry(path.relative_to(package_path).as_posix(), path.stat().st_size)
            for path in package_path.rglob("*")
            if path.is_file()
        ]
    if package_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(package_path) as archive:
            return [
                PackageEntry(info.filename, info.file_size)
                for info in archive.infolist()
                if not info.is_dir()
            ]
    if package_path.suffix.lower() == ".7z":
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("py7zr is required to preview .7z packages") from exc

        with py7zr.SevenZipFile(package_path) as archive:
            return [
                PackageEntry(info.filename, getattr(info, "uncompressed", 0) or 0)
                for info in archive.list()
                if not info.is_directory
            ]
    if package_path.suffix.lower() == ".iso":
        try:
            import pycdlib
        except ImportError as exc:
            raise RuntimeError("pycdlib is required to preview ISO packages") from exc

        iso = pycdlib.PyCdlib()
        entries: list[PackageEntry] = []
        try:
            iso.open(str(package_path))
            for root, _, files in iso.walk(joliet_path="/"):
                for name in files:
                    relative = f"{root.rstrip('/')}/{name}".lstrip("/")
                    record = iso.get_record(joliet_path=f"/{relative}")
                    entries.append(PackageEntry(relative, record.data_length))
            return entries
        finally:
            iso.close()
    raise RuntimeError(f"Entry listing is not implemented for {package_path.suffix}")


def read_entry_bytes(
    package_path: Path,
    entry_path: str,
    limit: int | None = 256 * 1024,
    temporary_root: Path | None = None,
) -> bytes:
    def read_limited(handle) -> bytes:
        return handle.read() if limit is None else handle.read(limit)

    if package_path.is_dir():
        target = package_path / entry_path
        with target.open("rb") as handle:
            return read_limited(handle)
    if package_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(package_path) as archive:
            with archive.open(entry_path) as handle:
                return read_limited(handle)
    if package_path.suffix.lower() == ".7z":
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("py7zr is required to preview .7z packages") from exc

        with temporary_directory("back-up-helper-preview-", temporary_root) as temp:
            temp_dir = Path(temp)
            with py7zr.SevenZipFile(package_path) as archive:
                archive.extract(path=temp_dir, targets=[entry_path])
            with (temp_dir / entry_path).open("rb") as handle:
                return read_limited(handle)
    if package_path.suffix.lower() == ".iso":
        try:
            import pycdlib
        except ImportError as exc:
            raise RuntimeError("pycdlib is required to preview ISO packages") from exc

        iso = pycdlib.PyCdlib()
        try:
            iso.open(str(package_path))
            payload = BytesIO()
            iso.get_file_from_iso_fp(payload, joliet_path="/" + entry_path.lstrip("/"))
            return payload.getvalue() if limit is None else payload.getvalue()[:limit]
        finally:
            iso.close()
    raise RuntimeError(f"File preview is not implemented for {package_path.suffix}")


def copy_entry_to(
    package_path: Path,
    entry_path: str,
    destination: Path,
    temporary_root: Path | None = None,
) -> Path:
    """Copy one known package entry to a caller-provided destination."""
    relative_path = PurePosixPath(entry_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unsafe package entry path: {entry_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if package_path.is_dir():
        shutil.copy2(package_path / entry_path, destination)
        return destination
    if package_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(package_path) as archive:
            with archive.open(entry_path) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
        return destination
    if package_path.suffix.lower() == ".7z":
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("py7zr is required to restore .7z packages") from exc

        with temporary_directory("back-up-helper-restore-", temporary_root) as temp:
            temp_dir = Path(temp)
            with py7zr.SevenZipFile(package_path) as archive:
                archive.extract(path=temp_dir, targets=[entry_path])
            shutil.copy2(temp_dir / entry_path, destination)
        return destination
    if package_path.suffix.lower() == ".iso":
        try:
            import pycdlib
        except ImportError as exc:
            raise RuntimeError("pycdlib is required to restore ISO packages") from exc

        iso = pycdlib.PyCdlib()
        try:
            iso.open(str(package_path))
            with destination.open("wb") as target:
                iso.get_file_from_iso_fp(target, joliet_path="/" + entry_path.lstrip("/"))
        finally:
            iso.close()
        return destination
    raise RuntimeError(f"Restore is not implemented for {package_path.suffix}")


def extract_package_to(
    package_path: Path,
    destination: Path,
    temporary_root: Path | None = None,
) -> Path:
    """Extract a whole package while rejecting paths that escape the destination."""
    entries = list_entries(package_path)
    for entry in entries:
        relative_path = PurePosixPath(entry.path.replace("\\", "/"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"Unsafe package entry path: {entry.path}")

    destination.mkdir(parents=True, exist_ok=True)
    if package_path.is_dir():
        for entry in entries:
            copy_entry_to(package_path, entry.path, destination / entry.path, temporary_root)
        return destination
    if package_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(package_path) as archive:
            for entry in entries:
                target = destination / PurePosixPath(entry.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry.path) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        return destination
    if package_path.suffix.lower() == ".7z":
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("py7zr is required to extract .7z packages") from exc

        with py7zr.SevenZipFile(package_path) as archive:
            archive.extractall(path=destination)
        return destination
    if package_path.suffix.lower() == ".iso":
        try:
            import pycdlib
        except ImportError as exc:
            raise RuntimeError("pycdlib is required to extract ISO packages") from exc

        iso = pycdlib.PyCdlib()
        try:
            iso.open(str(package_path))
            for entry in entries:
                target = destination / PurePosixPath(entry.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as output:
                    iso.get_file_from_iso_fp(output, joliet_path="/" + entry.path.lstrip("/"))
        finally:
            iso.close()
        return destination
    raise RuntimeError(f"Extraction is not implemented for {package_path.suffix}")
