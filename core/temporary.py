from __future__ import annotations

import tempfile
from pathlib import Path


def prepare_temporary_root(temporary_root: Path | None) -> Path | None:
    """Return a writable application work root, or ``None`` for the system default."""
    if temporary_root is None:
        return None
    root = temporary_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"Temporary root is not a directory: {root}")
    return root


def temporary_directory(prefix: str, temporary_root: Path | None = None):
    root = prepare_temporary_root(temporary_root)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=root)
