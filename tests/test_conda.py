from __future__ import annotations

from pathlib import Path

from inventory.conda import CondaEnvironment, CondaExportPlan


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
