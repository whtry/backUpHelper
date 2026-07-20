from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from inventory.conda import CondaEnvironment, CondaExportPlan, export_conda_environment_files


def test_conda_export_plan_shape() -> None:
    env = CondaEnvironment(name="demo", prefix=Path("C:/conda/envs/demo"))
    plan = CondaExportPlan(
        environment=env,
        environment_yml_command=["conda", "env", "export", "--prefix", str(env.prefix)],
        requirements_command=["conda", "list", "--prefix", str(env.prefix), "--export"],
        explicit_command=["conda", "list", "--prefix", str(env.prefix), "--explicit"],
        restore_command=["conda", "env", "create", "--file", "demo.environment.yml"],
    )

    assert "--export" in plan.requirements_command
    assert plan.restore_command[-1] == "demo.environment.yml"


def test_conda_environment_exports_write_portable_files(tmp_path: Path, monkeypatch) -> None:
    environment = CondaEnvironment(name="demo", prefix=Path("C:/conda/envs/demo"))
    plan = CondaExportPlan(
        environment=environment,
        environment_yml_command=["conda", "env", "export"],
        requirements_command=["conda", "list", "--export"],
        explicit_command=["conda", "list", "--explicit"],
        restore_command=["conda", "env", "create"],
    )

    def fake_run(command, *, stdout, **_kwargs):
        stdout.write("# exported " + command[-1] + "\n")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("inventory.conda.subprocess.run", fake_run)
    results = export_conda_environment_files(tmp_path / "inventory" / "conda", [plan])

    export_root = tmp_path / "inventory" / "conda" / "demo"
    assert results[0].errors == ()
    assert (export_root / "environment.yml").is_file()
    assert (export_root / "requirements.txt").is_file()
    assert (export_root / "explicit.txt").is_file()
