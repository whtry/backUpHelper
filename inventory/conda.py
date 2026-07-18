from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


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
