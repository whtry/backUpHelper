from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RegistryExportResult:
    key: str
    output_path: Path
    success: bool
    message: str


def export_registry_key(key: str, output_path: Path) -> RegistryExportResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["reg.exe", "export", key, str(output_path), "/y"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except OSError as exc:
        return RegistryExportResult(key, output_path, False, str(exc))

    message = (result.stdout or result.stderr).strip()
    return RegistryExportResult(key, output_path, result.returncode == 0, message)


def export_registry_keys(keys: list[str], output_dir: Path) -> list[RegistryExportResult]:
    results = []
    for index, key in enumerate(keys, start=1):
        safe_name = "".join(char if char.isalnum() else "_" for char in key)
        results.append(export_registry_key(key, output_dir / f"{index:03d}-{safe_name}.reg"))
    return results
