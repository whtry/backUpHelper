from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Iterator
from pathlib import Path

DEFAULT_EXCLUDES = (
    "**/__pycache__/**",
    "**/.git/objects/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/Cache/**",
    "**/Code Cache/**",
    "**/GPUCache/**",
    "**/Crashpad/**",
)


def should_exclude(path: Path, patterns: Iterable[str] = DEFAULT_EXCLUDES) -> bool:
    normalized = path.as_posix()
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def is_relative_path_excluded(path: Path, excluded_relative_paths: Iterable[str]) -> bool:
    normalized = path.as_posix().strip("/")
    for excluded in excluded_relative_paths:
        excluded_normalized = excluded.strip("/")
        if normalized == excluded_normalized or normalized.startswith(f"{excluded_normalized}/"):
            return True
    return False


def iter_files(
    root: Path,
    exclude_patterns: Iterable[str] = DEFAULT_EXCLUDES,
    excluded_relative_paths: Iterable[str] = (),
) -> Iterator[Path]:
    if root.is_file():
        if not should_exclude(root, exclude_patterns):
            yield root
        return

    if not root.exists():
        return

    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = Path(path.name)
        if is_relative_path_excluded(relative, excluded_relative_paths):
            continue
        if path.is_file() and not should_exclude(path, exclude_patterns):
            yield path
