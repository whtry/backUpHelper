from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from core.serialization import load_json


@dataclass(frozen=True)
class PackageEntry:
    path: str
    size: int


def read_manifest(package_path: Path) -> dict:
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

        with TemporaryDirectory(prefix="back-up-helper-preview-") as temp:
            temp_dir = Path(temp)
            with py7zr.SevenZipFile(package_path) as archive:
                archive.extract(path=temp_dir, targets=["manifest.json"])
            import json

            return json.loads((temp_dir / "manifest.json").read_text(encoding="utf-8"))
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
    raise RuntimeError(f"Entry listing is not implemented for {package_path.suffix}")


def read_entry_bytes(package_path: Path, entry_path: str, limit: int = 256 * 1024) -> bytes:
    if package_path.is_dir():
        target = package_path / entry_path
        with target.open("rb") as handle:
            return handle.read(limit)
    if package_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(package_path) as archive:
            with archive.open(entry_path) as handle:
                return handle.read(limit)
    if package_path.suffix.lower() == ".7z":
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("py7zr is required to preview .7z packages") from exc

        with TemporaryDirectory(prefix="back-up-helper-preview-") as temp:
            temp_dir = Path(temp)
            with py7zr.SevenZipFile(package_path) as archive:
                archive.extract(path=temp_dir, targets=[entry_path])
            with (temp_dir / entry_path).open("rb") as handle:
                return handle.read(limit)
    raise RuntimeError(f"File preview is not implemented for {package_path.suffix}")
