from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from core.cancellation import raise_if_cancelled


def _hidden_subprocess_options() -> dict[str, object]:
    """Prevent Conda's Windows launcher from flashing a console window."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


@dataclass(frozen=True)
class CondaEnvironment:
    name: str
    prefix: Path


@dataclass(frozen=True)
class CondaExportPlan:
    environment: CondaEnvironment
    environment_yml_command: list[str]
    requirements_command: list[str]
    explicit_command: list[str]
    restore_command: list[str]


@dataclass(frozen=True)
class CondaExportResult:
    environment: CondaEnvironment
    exported_files: tuple[str, ...]
    errors: tuple[str, ...]


def find_conda() -> Path | None:
    resolved = shutil.which("conda")
    return Path(resolved) if resolved else None


def list_conda_environments(conda_exe: Path | None = None) -> list[CondaEnvironment]:
    conda_exe = conda_exe or find_conda()
    if not conda_exe:
        return []

    try:
        result = subprocess.run(
            [str(conda_exe), "env", "list", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            **_hidden_subprocess_options(),
        )
    except (OSError, subprocess.SubprocessError):
        return []

    data = json.loads(result.stdout)
    envs = []
    for raw_prefix in data.get("envs", []):
        prefix = Path(raw_prefix)
        envs.append(CondaEnvironment(name=prefix.name, prefix=prefix))
    return envs


def build_conda_export_plans(conda_exe: Path | None = None) -> list[CondaExportPlan]:
    conda_exe = conda_exe or find_conda()
    if not conda_exe:
        return []

    executable = str(conda_exe)
    plans = []
    for environment in list_conda_environments(conda_exe):
        prefix_arg = str(environment.prefix)
        plans.append(
            CondaExportPlan(
                environment=environment,
                environment_yml_command=[
                    executable,
                    "env",
                    "export",
                    "--prefix",
                    prefix_arg,
                    "--no-builds",
                ],
                requirements_command=[
                    executable,
                    "list",
                    "--prefix",
                    prefix_arg,
                    "--export",
                ],
                explicit_command=[
                    executable,
                    "list",
                    "--prefix",
                    prefix_arg,
                    "--explicit",
                ],
                restore_command=[
                    executable,
                    "env",
                    "create",
                    "--file",
                    f"{environment.name}.environment.yml",
                ],
            )
        )
    return plans


def export_conda_environment_files(
    destination: Path,
    plans: list[CondaExportPlan],
    cancel_event: Event | None = None,
) -> list[CondaExportResult]:
    """Write portable Conda exports alongside the command plan for each environment."""
    destination.mkdir(parents=True, exist_ok=True)
    results: list[CondaExportResult] = []
    for index, plan in enumerate(plans, start=1):
        raise_if_cancelled(cancel_event)
        safe_name = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in plan.environment.name
        ) or f"environment-{index}"
        environment_dir = destination / safe_name
        exports = {
            "environment.yml": plan.environment_yml_command,
            "requirements.txt": plan.requirements_command,
            "explicit.txt": plan.explicit_command,
        }
        written: list[str] = []
        errors: list[str] = []
        for filename, command in exports.items():
            raise_if_cancelled(cancel_event)
            target = environment_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("w", encoding="utf-8", newline="\n") as handle:
                    result = subprocess.run(
                        command,
                        check=False,
                        stdout=handle,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=120,
                        **_hidden_subprocess_options(),
                    )
                if result.returncode:
                    target.unlink(missing_ok=True)
                    errors.append(f"{filename}: {result.stderr.strip() or result.returncode}")
                else:
                    written.append(target.relative_to(destination.parent).as_posix())
            except (OSError, subprocess.SubprocessError) as exc:
                target.unlink(missing_ok=True)
                errors.append(f"{filename}: {exc}")
        results.append(
            CondaExportResult(
                environment=plan.environment,
                exported_files=tuple(written),
                errors=tuple(errors),
            )
        )
    return results
