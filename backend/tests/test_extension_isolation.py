"""Kernel-isolation wall for health's spawn seam (plan 7 W4 / plan 39).

The fixture file (``contracts/sandbox-isolation.fixtures.json``) is the
normative probe set — vendored, never edited here; this wall asserts the
HOST side of it: the escape-matrix contract holds, the host-profile surface
reports DETECTION (never a constant), and the spawn seam answers the typed
``unsupported`` + reason for every hostile composition (profile without the
kernel grade, platform without the primitive, non-T2 content) instead of
spawning.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.extensions import contract, isolation
from app.extensions.errors import ExtensionError
from app.extensions.runtime import sandbox
from app.models.enums import ExtensionVersionStatus
from app.models.extension_source_model import ExtensionSource, ExtensionSourceVersion

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


def _t2_version() -> ExtensionSourceVersion:
    return ExtensionSourceVersion(
        source_id=uuid.uuid4(),
        digest="a" * 64,
        manifest_digest="b" * 64,
        envelope={"id": "demo-t2", "entry": "extension.py", "kind": "connector"},
        status=ExtensionVersionStatus.APPROVED,
        contract_version="extension/v1",
    )


class TestEscapeMatrix:
    def test_escape_matrix_is_the_contract(self):
        cases = isolation.escape_matrix(CONTRACTS_DIR)
        groups = {case["group"] for case in cases}
        for required in isolation_contract_groups():
            assert required in groups, f"isolation matrix lost the {required!r} group"
        by_name = {case["name"]: case for case in cases}
        assert by_name["fs-read-etc-passwd"]["expect"]["kernel"] == "denied"
        assert by_name["net-connect-arbitrary"]["expect"]["kernel"] == "denied"
        assert by_name["net-connect-arbitrary"]["expect"]["process"] == "disclosed", (
            "process grade must DISCLAIM what it cannot deny (disclosure law)"
        )
        assert by_name["fs-read-etc-passwd"]["expect"]["none"] == "self-disabled"

    def test_posture_never_overstates_the_boundary(self):
        assert isolation.posture() == ("kernel" if isolation.available() else "none")

    def test_wrapper_contains_the_kernel_boundary(self):
        if not isolation.available():
            pytest.skip("bubblewrap unavailable")
        argv = isolation.wrap_child_command(
            interpreter=[sys.executable],
            child_path=Path("/tmp/x.py"),
            content_dir=Path("/tmp"),
            child_args=["--content-dir", "/tmp"],
        )
        assert argv[0].endswith("bwrap")
        assert "--unshare-all" in argv
        assert "--die-with-parent" in argv
        assert "--clearenv" in argv
        assert "--ro-bind" in argv
        assert "/ext" in argv


def isolation_contract_groups() -> tuple[str, ...]:
    return (
        "fs-read-outside",
        "fs-write-outside",
        "net-connect",
        "net-dns",
        "proc-spawn",
        "env-read",
        "watchdog-kill",
        "orphan-after-parent-death",
        "memory-bomb",
        "cpu-loop",
    )


class TestHostProfileSurface:
    async def test_host_profile_reports_detection_and_declared_data(self):
        from httpx import ASGITransport, AsyncClient

        from app.main import app
        from tests._auth_helpers import auth_headers, create_user

        user = await create_user(full_name="Profile Probe")
        headers = await auth_headers(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/integrations/host-profile", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["t2Isolation"] == isolation.posture(), "detection, never a constant"
        declared = body["t2IsolationByPlatform"]
        assert declared[sys.platform] == "none", "the charter declaration rides as data"
        assert body["disclosures"] == ["t3PerimeterDisclosure"]


class TestSpawnSeam:
    async def test_t2_spawn_refused_with_the_typed_profile_reason(self, db_session, tmp_path):
        """Health's charter row declares ``linux: none`` — the spawn answers
        typed ``unsupported`` + the profile reason (never a silent spawn)."""
        plan_error = None
        try:
            await sandbox.spawn_plan(
                db_session,
                _t2_version(),
                interpreter=[sys.executable],
                child_path=tmp_path / "child.py",
                content_dir=tmp_path,
            )
        except ExtensionError as exc:
            plan_error = exc
        assert plan_error is not None, "an undeclared platform never spawns"
        assert plan_error.code == "unsupported"
        assert "t2Isolation" in plan_error.detail
        assert "none" in plan_error.detail

    async def test_posture_row_recorded_even_when_refused(self, db_session, tmp_path):
        from sqlalchemy import select

        from app.models.extension_source_model import ExtensionIsolationPosture

        source = ExtensionSource(
            tenant_id=None,
            kind="git",
            uri=f"https://git.example.com/ext/{uuid.uuid4().hex[:8]}.git",
            trust_tier="T2",
        )
        db_session.add(source)
        await db_session.flush()
        version = ExtensionSourceVersion(
            source_id=source.id,
            digest="c" * 64,
            manifest_digest="d" * 64,
            envelope={"id": "demo-t2", "entry": "extension.py", "kind": "connector"},
            status=ExtensionVersionStatus.APPROVED,
            contract_version="extension/v1",
        )
        db_session.add(version)
        await db_session.flush()

        with pytest.raises(ExtensionError):
            await sandbox.spawn_plan(
                db_session,
                version,
                interpreter=[sys.executable],
                child_path=tmp_path / "child.py",
                content_dir=tmp_path,
            )
        row = (
            await db_session.execute(
                select(ExtensionIsolationPosture).where(
                    ExtensionIsolationPosture.version_id == version.id
                )
            )
        ).scalar_one()
        assert row.derived_tier.value == "T2"
        assert row.posture["grade"] == isolation.posture()
        assert row.posture["probed_from"] == "detection"

    async def test_kernel_declared_but_primitive_missing_still_refuses(
        self, db_session, tmp_path, monkeypatch
    ):
        from app.extensions.registry import _profile

        profile = dict(_profile())
        profile["t2IsolationByPlatform"] = {sys.platform: "kernel"}
        monkeypatch.setattr("app.extensions.registry._profile", lambda: profile)
        monkeypatch.setattr(isolation, "available", lambda: False)
        monkeypatch.setattr(isolation, "posture", lambda: isolation.GRADE_NONE)

        with pytest.raises(ExtensionError) as excinfo:
            await sandbox.spawn_plan(
                db_session,
                _t2_version(),
                interpreter=[sys.executable],
                child_path=tmp_path / "child.py",
                content_dir=tmp_path,
            )
        assert excinfo.value.code == "unsupported"
        assert "detection reports grade" in excinfo.value.detail

    async def test_non_t2_content_never_spawns_sandboxed(self, db_session, tmp_path):
        version = ExtensionSourceVersion(
            source_id=uuid.uuid4(),
            digest="e" * 64,
            manifest_digest="f" * 64,
            envelope={"id": "demo-t0", "kind": "connector"},
            status=ExtensionVersionStatus.APPROVED,
            contract_version="extension/v1",
        )
        with pytest.raises(ExtensionError) as excinfo:
            await sandbox.spawn_plan(
                db_session,
                version,
                interpreter=[sys.executable],
                child_path=tmp_path / "child.py",
                content_dir=tmp_path,
            )
        assert excinfo.value.code == "unsupported"
        assert contract.derive_tier(version.envelope, bundled=False) == "T0"

    async def test_kernel_profile_and_detection_spawn_the_wrapped_argv(
        self, db_session, tmp_path, monkeypatch
    ):
        from app.extensions.registry import _profile

        profile = dict(_profile())
        profile["t2IsolationByPlatform"] = {sys.platform: "kernel"}
        monkeypatch.setattr("app.extensions.registry._profile", lambda: profile)
        if not isolation.available():
            monkeypatch.setattr(isolation, "available", lambda: True)
            monkeypatch.setattr(isolation, "posture", lambda: isolation.GRADE_KERNEL)
            monkeypatch.setattr(isolation, "BWRAP", "/usr/bin/bwrap", raising=False)

        child = tmp_path / "child.py"
        child.write_text("# content\n")
        plan = await sandbox.spawn_plan(
            db_session,
            _t2_version(),
            interpreter=[sys.executable],
            child_path=child,
            content_dir=tmp_path,
            child_args=["--content-dir", str(tmp_path)],
        )
        assert plan.grade == "kernel"
        assert plan.argv[0].endswith("bwrap")
        assert "--unshare-all" in plan.argv
        assert plan.argv[plan.argv.index("--content-dir") + 1] == "/ext", (
            "the real content path is rewritten inside the sandbox"
        )
