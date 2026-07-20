from __future__ import annotations

from pathlib import Path

from preview.package_reader import read_entry_bytes

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".csv",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".ps1",
    ".bat",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm"}


def preview_entry_text(
    package_path: Path,
    entry_path: str,
    temporary_root: Path | None = None,
) -> str:
    suffix = Path(entry_path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "Image file. Select Extract to open it with the local system viewer."
    if suffix in VIDEO_EXTENSIONS:
        return "Video file. Select Extract to open it with the local system player."
    if suffix not in TEXT_EXTENSIONS and not entry_path.endswith("manifest.json"):
        return "Binary or unknown file. The browser shows its package path and size only."

    raw = read_entry_bytes(package_path, entry_path, temporary_root=temporary_root)
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return "The text encoding could not be identified."
