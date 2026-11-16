"""Restore script shell contract (plan 65 / ADR-0063 §4, §7).

The restore is a **throwaway-target** procedure: it must refuse to write into
the live database, require an explicit --target, refuse to proceed without
confirmation, and carry the Timescale pre/post-restore hooks. These tests run
without Docker: the refusal paths sit before any docker invocation by design
(so an operator without a running stack still gets the safety stops, and CI
can enforce the contract).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESTORE = REPO / "scripts" / "restore.sh"
BACKUP = REPO / "scripts" / "backup.sh"


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RESTORE), *args],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_parses() -> None:
    subprocess.run(["bash", "-n", str(RESTORE)], check=True)
    subprocess.run(["bash", "-n", str(BACKUP)], check=True)


def test_no_args_is_usage_error() -> None:
    result = _run([])
    assert result.returncode == 1
    assert "--target" in result.stderr


def test_missing_target_is_usage_error(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"")
    result = _run([str(archive)])
    assert result.returncode == 1
    assert "--target" in result.stderr


def test_refuses_live_database(tmp_path: Path) -> None:
    """Restoring into the live DB is an ADR-0063 §7 violation — hard refusal.

    The refusal must happen before docker/compose is touched (exit 2 with no
    docker error text), so the check works on any machine.
    """
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"")
    for env in (
        {"POSTGRES_DB": "neuronection_health"},
        {
            "POSTGRES_DB": "something_else",
            "HA_DATABASE_URL": "postgresql+asyncpg://app:pw@db:5432/neuronection_health",
        },
    ):
        result = _run(
            [str(archive), "--target", "neuronection_health", "--yes"],
            env=env,
        )
        assert result.returncode == 2, (env, result.stdout, result.stderr)
        assert "LIVE database" in result.stderr


def test_refuses_system_databases(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"")
    for target in ("postgres", "template0", "template1"):
        result = _run([str(archive), "--target", target, "--yes"])
        assert result.returncode == 2, target
        assert "system database" in result.stderr


def test_refuses_test_database(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"")
    result = _run(
        [str(archive), "--target", "neuronection_health_test", "--yes"],
        env={"POSTGRES_DB": "neuronection_health", "POSTGRES_TEST_DB": "neuronection_health_test"},
    )
    assert result.returncode == 2
    assert "test database" in result.stderr


def test_refuses_non_identifier_target(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"")
    result = _run([str(archive), "--target", "Bad Name; DROP TABLE x", "--yes"])
    assert result.returncode == 1
    assert "identifier" in result.stderr


def test_refuses_without_confirmation(tmp_path: Path) -> None:
    """No tty and no --yes → refuse before touching docker."""
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"")
    result = _run(
        [str(archive), "--target", "ha_restore_test"],
        env={"POSTGRES_DB": "neuronection_health"},
    )
    assert result.returncode == 2
    assert "--yes" in result.stderr


def test_unreachable_archive_refused() -> None:
    result = _run(
        ["does-not-exist.tar.gz", "--target", "ha_restore_test", "--yes"],
        env={"POSTGRES_DB": "neuronection_health"},
    )
    assert result.returncode == 2


def test_carries_the_timescale_aware_procedure() -> None:
    """ADR-0063 §7: health restore without pre/post hooks is a violation."""
    source = RESTORE.read_text()
    for token in (
        "timescaledb_pre_restore()",
        "timescaledb_post_restore()",
        "CREATE EXTENSION IF NOT EXISTS timescaledb",
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "--no-owner",
        "ANALYZE;",
        "add_continuous_aggregate_policy",
        "add_retention_policy",
        "add_compression_policy",
        "ALTER DEFAULT PRIVILEGES",
        "replay_erasure_ledger",
        "pg_policies",
        "telemetry_hourly_scoped",
        "audit_chain_heads",
        "--drop-existing",
        "--exit-on-error",
    ):
        assert token in source, f"restore.sh lost required token: {token}"
    # No parallel restore: Timescale does not support -j on pg_restore.
    for line in source.splitlines():
        if "pg_restore" in line:
            assert "-j" not in line, f"pg_restore must stay single-threaded: {line.strip()}"
    # The live-DB pattern from the old script must be gone.
    assert "--clean" not in source, "pg_restore --clean into a live DB is retired"


def test_backup_script_points_at_the_new_restore_contract() -> None:
    text = BACKUP.read_text()
    assert "--target" in text, "backup.sh must advertise the throwaway-target restore"
