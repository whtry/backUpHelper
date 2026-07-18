from __future__ import annotations

import ctypes
from pathlib import Path


def list_windows_volumes() -> list[Path]:
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except AttributeError:
        return []

    volumes: list[Path] = []
    for index in range(26):
        if bitmask & (1 << index):
            letter = chr(ord("A") + index)
            volumes.append(Path(f"{letter}:/"))
    return volumes
