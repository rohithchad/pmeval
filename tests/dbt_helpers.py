"""Helpers for running dbt against fixture warehouses from pytest."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

DBT = shutil.which("dbt") or str(Path(sys.executable).parent / "dbt")
DBT_PROJECT = Path(__file__).parent.parent / "dbt_project"


def dbt_available() -> bool:
    return Path(DBT).exists()


def run_dbt(root: Path, warehouse_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a dbt command against one warehouse file, keeping dbt's output inside `root`."""
    env = {
        **os.environ,
        "PMEVAL_WAREHOUSE_PATH": str(warehouse_path),
        "DBT_TARGET_PATH": str(root / "target"),
    }
    return subprocess.run(
        [DBT, *args, "--profiles-dir", ".", "--log-path", str(root / "logs")],
        cwd=DBT_PROJECT,
        env=env,
        capture_output=True,
        text=True,
    )
