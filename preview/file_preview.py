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


def preview_entry_text(package_path: Path, entry_path: str) -> str:
    suffix = Path(entry_path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "图片文件，可在后续版本中以 Qt 图片预览面板打开。"
    if suffix in VIDEO_EXTENSIONS:
        return "视频文件，可在后续版本中通过 Qt Multimedia/FFmpeg 生成预览。"
    if suffix not in TEXT_EXTENSIONS and not entry_path.endswith("manifest.json"):
        return "二进制或未知类型文件，当前仅显示包内路径和大小。"

    raw = read_entry_bytes(package_path, entry_path)
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return "文本编码无法识别。"
