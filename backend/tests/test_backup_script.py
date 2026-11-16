"""Backup script shell contract: resolved DB name/owner are not corrupt."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

BACKUP = Path(__file__).resolve().parents[2] / "scripts" / "backup.sh"


def _resolve(env: dict[str, str]) -> str:
    source = BACKUP.read_text()
    assignments = [
        line
        for line in source.splitlines()
        if re.match(r"^(DB_NAME|DB_OWNER)=", line)
    ]
    assert len(assignments) == 2, assignments
    script = (
        "set -euo pipefail\n"
        + "\n".join(assignments)
        + '\nprintf "%s|%s" "$DB_NAME" "$DB_OWNER"'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_script_parses() -> None:
    subprocess.run(["bash", "-n", str(BACKUP)], check=True)


def test_resolved_defaults_are_two_distinct_variables() -> None:
    # An empty value is "unset" for bash's ``${VAR:-default}`` expansion and
    # for the compose `.env` loader that feeds this script.
    assert _resolve({"POSTGRES_DB": ""}) == "neuronection_health|neuronection_health_owner"


def test_resolved_custom_database_name() -> None:
    assert _resolve({"POSTGRES_DB": "custom_db"}) == "custom_db|neuronection_health_owner"
