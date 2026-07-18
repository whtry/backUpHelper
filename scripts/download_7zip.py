from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

DOWNLOAD_PAGE = "https://www.7-zip.org/download.html"
BASE_URL = "https://www.7-zip.org/"


def absolute_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return BASE_URL + href.lstrip("/")


def latest_installer_url(arch: str) -> str:
    with urllib.request.urlopen(DOWNLOAD_PAGE, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")

    extensions = ["msi", "exe"] if arch in {"x64", "x86"} else ["exe"]
    for extension in extensions:
        suffix = {
            "x64": rf"-x64\.{extension}",
            "arm64": rf"-arm64\.{extension}",
            "x86": rf"\.{extension}",
        }[arch]
        pattern = rf'href="(?P<href>[^"]*7z(?P<version>\d+){suffix})"'
        matches = list(re.finditer(pattern, html, re.IGNORECASE))
        if matches:
            newest = max(matches, key=lambda match: int(match.group("version")))
            return absolute_url(newest.group("href"))
    raise RuntimeError(f"Could not find latest 7-Zip installer for {arch}")


def current_arch() -> str:
    machine = platform.machine().lower()
    if "arm" in machine and "64" in machine:
        return "arm64"
    if machine in {"x86", "i386", "i686"}:
        return "x86"
    return "x64"


def download_latest_7zip(target_dir: Path, arch: str = "auto") -> Path:
    if arch == "auto":
        arch = current_arch()
    if arch not in {"x86", "x64", "arm64"}:
        raise ValueError("Architecture must be one of: auto, x86, x64, arm64")
    target_dir.mkdir(parents=True, exist_ok=True)
    url = latest_installer_url(arch)
    installer_name = url.rsplit("/", 1)[-1]
    with tempfile.TemporaryDirectory(prefix="back-up-helper-7zip-") as temp:
        installer_path = Path(temp) / installer_name
        print(f"Downloading {url}")
        urllib.request.urlretrieve(url, installer_path)
        if installer_path.suffix.lower() == ".msi":
            print(f"Extracting MSI {installer_path} -> {target_dir}")
            subprocess.run(
                ["msiexec", "/a", str(installer_path), f"TARGETDIR={target_dir.resolve()}", "/qn"],
                check=True,
            )
            normalize_msi_layout(target_dir)
        else:
            print(f"Extracting installer {installer_path} -> {target_dir}")
            extract_with_7z(installer_path, target_dir)
    readme = target_dir / "README.txt"
    readme.write_text(
        "7-Zip Extra is downloaded from https://www.7-zip.org/download.html\n"
        "License information: https://www.7-zip.org/license.txt\n"
        f"Architecture: {arch}\n"
        "This directory is intentionally ignored by Git.\n",
        encoding="utf-8",
    )
    return target_dir


def normalize_msi_layout(target_dir: Path) -> None:
    exe_candidates = list(target_dir.rglob("7z.exe"))
    if not exe_candidates:
        raise RuntimeError("MSI extraction completed but 7z.exe was not found")
    source_dir = exe_candidates[0].parent
    for path in source_dir.iterdir():
        target = target_dir / path.name
        if path.resolve() != target.resolve() and path.is_file():
            target.write_bytes(path.read_bytes())
    files_dir = target_dir / "Files"
    if files_dir.exists():
        shutil.rmtree(files_dir)
    for installer in target_dir.glob("*.msi"):
        installer.unlink()


def extract_with_7z(installer_path: Path, target_dir: Path) -> None:
    executable = find_real_7z()
    if not executable:
        raise RuntimeError("A real 7z/7za/7zz executable is required to extract this installer")
    subprocess.run(
        [executable, "x", str(installer_path), f"-o{target_dir.resolve()}", "-y"],
        check=True,
    )


def find_real_7z() -> str | None:
    from shutil import which

    for name in ("7zz", "7za", "7z", "7z.exe"):
        candidate = which(name)
        if candidate and "WindowsApps" not in candidate:
            return candidate
    return None


def main() -> int:
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tools") / "7zip"
    arch = sys.argv[2] if len(sys.argv) > 2 else "auto"
    download_latest_7zip(target_dir, arch)
    print(f"7-Zip tools are ready in {target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
