from __future__ import annotations

import shutil
from pathlib import Path

from core.models import InstalledApplication


def normalize_icon_path(raw_icon: str | None) -> Path | None:
    if not raw_icon:
        return None
    value = raw_icon.strip().strip('"')
    if "," in value:
        value = value.rsplit(",", 1)[0].strip().strip('"')
    if value.startswith("@"):
        value = value[1:]
    path = Path(value)
    return path if path.exists() else None


def copy_application_icons(
    applications: list[InstalledApplication],
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    for index, app in enumerate(applications, start=1):
        source = normalize_icon_path(app.icon_path)
        if not source or source.suffix.lower() not in {".ico", ".exe", ".dll"}:
            continue
        suffix = ".ico" if source.suffix.lower() == ".ico" else source.suffix.lower()
        safe_name = "".join(char if char.isalnum() else "_" for char in app.name)[:80]
        target = output_dir / f"{index:04d}-{safe_name}{suffix}"
        try:
            shutil.copy2(source, target)
        except OSError:
            continue
        copied[app.name] = target.relative_to(output_dir.parent).as_posix()
    return copied
