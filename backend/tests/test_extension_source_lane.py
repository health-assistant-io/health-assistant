"""The imported-extension source lane — registration seam (plan 7 W2).

Non-vacuity pins, all against the real rows:

* a T3 import registers through the ONE registration decision and lands a
  ``pending`` version + a DETECTION posture row;
* a tampered re-import creates a NEW ``pending`` version and the running
  instance's active digest never moves (no auto-upgrade);
* a caller-claimed tier that contradicts ``derive_tier`` is the typed
  ``provenance derives tier …`` problem — recorded IN the version row (a
  self-disable is a row, never a log-and-skip);
* the T2 lane on today's health profile answers the typed profile refusal
  in the row (charter data decides, the lane persists honestly);
* local dev sources are allowlist- and boot-gated (fail-closed);
* git sources sync to the typed ``not-ready`` with the failure recorded on
  the source row — pins untouched;
* the bundled registry's rejections surface through
  ``registration_failures()``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions import isolation, lifecycle
from app.extensions.errors import ExtensionError
from app.models.enums import ExtensionVersionStatus
from app.models.extension_source_model import (
    ExtensionIsolationPosture,
    ExtensionSource,
)


def _t3_manifest(extension_id: str = "demo-t3-mcp", version: str = "1.0.0") -> dict:
    return {
        "apiVersion": "extension/v1",
        "kindVersion": 1,
        "entry": "extension.py",
        "mcp": {"transport": "stdio"},
        "kind": "connector",
        "supportsIncremental": True,
        "id": extension_id,
        "version": version,
        "author": "Example Corp",
        "license": "MIT",
        "repository": f"https://git.example.com/ext/{extension_id}",
        "capabilities": ["sync"],
        "itemSchema": {"type": "object", "properties": {"external-id": {"type": "string"}}},
        "i18n": {
            "defaultLocale": "en",
            "locales": {"en": {"title": "Demo T3 MCP", "summary": "A demo external MCP connector"}},
        },
    }


def _t2_manifest(extension_id: str = "demo-t2-sandbox") -> dict:
    manifest = {
        "apiVersion": "extension/v1",
        "kindVersion": 1,
        "entry": "extension.py",
        "kind": "connector",
        "supportsIncremental": True,
        "id": extension_id,
        "version": "1.0.0",
        "author": "Example Corp",
        "license": "MIT",
        "repository": f"https://git.example.com/ext/{extension_id}",
        "capabilities": ["sync"],
        "itemSchema": {"type": "object", "properties": {"external-id": {"type": "string"}}},
        "i18n": {
            "defaultLocale": "en",
            "locales": {"en": {"title": "Demo T2", "summary": "A demo sandboxed connector"}},
        },
    }
    return manifest


def _write_bundle(root: Path, manifest: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "extension.py").write_text("# demo entry\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def local_source_roots(tmp_path, monkeypatch):
    """A dev-boot allowlisted root + the settings env pointing at it."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "HA_APP_ENV", "development")
    monkeypatch.setattr(settings, "HA_EXTENSION_LOCAL_SOURCE_ROOTS", str(tmp_path))
    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "data"))
    return tmp_path


_REF_BRANCH = {"strategy": "branch", "value": "main"}


async def _make_source(db, document: dict, tier: str = "T3") -> ExtensionSource:
    return await lifecycle.register_source(db, document=document, trust_tier=tier)


class TestRegistrationSeam:
    async def test_t3_import_lands_pending_version_and_detection_posture(
        self, db_session, local_source_roots
    ):
        bundle = _write_bundle(local_source_roots / "bundle-v1", _t3_manifest(version="1.0.0"))
        source = await _make_source(
            db_session, {"local": {"path": str(bundle)}, "ref": _REF_BRANCH}
        )
        versions = await lifecycle.sync_source(db_session, source)

        assert len(versions) == 1
        version = versions[0]
        assert version.status is ExtensionVersionStatus.PENDING
        assert version.reason is None
        assert version.contract_version == "extension/v1"
        assert version.manifest_digest
        assert version.digest == lifecycle.digest_tree(bundle)

        posture = (
            await db_session.execute(
                select(ExtensionIsolationPosture).where(
                    ExtensionIsolationPosture.version_id == version.id
                )
            )
        ).scalar_one()
        assert posture.derived_tier.value == "T3"
        assert posture.requested_tier.value == "T3"
        assert posture.posture["probed_from"] == "detection"
        assert posture.posture["grade"] == isolation.posture()

    async def test_tampered_reimport_creates_new_pending_and_never_auto_upgrades(
        self, db_session, local_source_roots
    ):
        bundle = _write_bundle(local_source_roots / "bundle-a", _t3_manifest(version="1.0.0"))
        source = await _make_source(
            db_session, {"local": {"path": str(bundle)}, "ref": _REF_BRANCH}
        )
        v1 = (await lifecycle.sync_source(db_session, source))[0]
        await lifecycle.approve_version(db_session, v1, approved_by=uuid.uuid4())
        await db_session.flush()

        active_before = await lifecycle.active_version(db_session, source)
        assert active_before is not None and active_before.id == v1.id

        # The tampered re-import: the source's content changed on disk.
        _write_bundle(bundle, _t3_manifest(version="9.9.9"))
        versions = await lifecycle.sync_source(db_session, source)
        assert len(versions) == 1
        incoming = versions[0]
        assert incoming.id != v1.id, "a digest change is a NEW version row"
        assert incoming.status is ExtensionVersionStatus.PENDING, "re-approval required"
        assert incoming.digest != v1.digest

        still_active = await lifecycle.active_version(db_session, source)
        assert still_active.id == v1.id, "the running instance refuses auto-upgrade"

    async def test_claimed_tier_contradiction_is_typed_and_lives_in_the_row(
        self, db_session, local_source_roots
    ):
        bundle = _write_bundle(local_source_roots / "bundle-t3", _t3_manifest())
        source = await _make_source(
            db_session,
            {"local": {"path": str(bundle)}, "ref": _REF_BRANCH},
            tier="T2",
        )
        version = (await lifecycle.sync_source(db_session, source))[0]
        assert version.status is ExtensionVersionStatus.REJECTED
        assert version.reason is not None
        assert "provenance derives tier T3" in version.reason
        assert "caller claims tier 'T2'" in version.reason

    async def test_t2_lane_answers_the_typed_profile_refusal_in_the_row(
        self, db_session, local_source_roots
    ):
        bundle = _write_bundle(local_source_roots / "bundle-t2", _t2_manifest())
        source = await _make_source(
            db_session,
            {"local": {"path": str(bundle)}, "ref": _REF_BRANCH},
            tier="T2",
        )
        version = (await lifecycle.sync_source(db_session, source))[0]
        assert version.status is ExtensionVersionStatus.REJECTED
        assert "tier 'T2' is not hosted by this profile" in (version.reason or "")

    async def test_identical_reimport_is_idempotent(self, db_session, local_source_roots):
        bundle = _write_bundle(local_source_roots / "bundle-idem", _t3_manifest())
        source = await _make_source(
            db_session, {"local": {"path": str(bundle)}, "ref": _REF_BRANCH}
        )
        first = (await lifecycle.sync_source(db_session, source))[0]
        second = (await lifecycle.sync_source(db_session, source))[0]
        assert first.id == second.id

    async def test_rejected_version_cannot_be_approved(self, db_session, local_source_roots):
        bundle = _write_bundle(local_source_roots / "bundle-t2", _t2_manifest())
        source = await _make_source(
            db_session,
            {"local": {"path": str(bundle)}, "ref": _REF_BRANCH},
            tier="T2",
        )
        version = (await lifecycle.sync_source(db_session, source))[0]
        with pytest.raises(ExtensionError) as excinfo:
            await lifecycle.approve_version(db_session, version, approved_by=None)
        assert excinfo.value.code == "invalid"

    async def test_symlinked_entry_never_registers(self, db_session, local_source_roots):
        manifest = _t3_manifest(extension_id="demo-symlink")
        bundle = _write_bundle(local_source_roots / "bundle-sym", manifest)
        outside = local_source_roots / "outside.py"
        outside.write_text("# evil\n", encoding="utf-8")
        (bundle / "extension.py").unlink()
        (bundle / "extension.py").symlink_to(outside)
        source = await _make_source(
            db_session, {"local": {"path": str(bundle)}, "ref": _REF_BRANCH}
        )
        version = (await lifecycle.sync_source(db_session, source))[0]
        assert version.status is ExtensionVersionStatus.REJECTED
        # Symlinks are digest-excluded (never materialized), so the entry is
        # uncovered — the registration decision refuses it, never follows it.
        assert "not a regular file" in (version.reason or "")
        assert "extension.py" in (version.reason or "")


class TestLocalGating:
    async def test_local_kind_fail_closed_without_roots(self, db_session, monkeypatch, tmp_path):
        from app.core.config import settings

        monkeypatch.setattr(settings, "HA_APP_ENV", "development")
        monkeypatch.setattr(settings, "HA_EXTENSION_LOCAL_SOURCE_ROOTS", "")
        with pytest.raises(ExtensionError) as excinfo:
            await lifecycle.register_source(
                db_session,
                document={"local": {"path": str(tmp_path / "x")}, "ref": _REF_BRANCH},
                trust_tier="T2",
            )
        assert excinfo.value.code == "path-denied"

    async def test_local_kind_fail_closed_in_production(self, db_session, monkeypatch, tmp_path):
        from app.core.config import settings

        monkeypatch.setattr(settings, "HA_APP_ENV", "production")
        monkeypatch.setattr(settings, "HA_EXTENSION_LOCAL_SOURCE_ROOTS", str(tmp_path))
        with pytest.raises(ExtensionError) as excinfo:
            await lifecycle.register_source(
                db_session,
                document={"local": {"path": str(tmp_path / "x")}, "ref": _REF_BRANCH},
                trust_tier="T2",
            )
        assert excinfo.value.code == "path-denied"

    async def test_local_path_outside_roots_refused(self, db_session, monkeypatch, tmp_path):
        from app.core.config import settings

        monkeypatch.setattr(settings, "HA_APP_ENV", "development")
        monkeypatch.setattr(settings, "HA_EXTENSION_LOCAL_SOURCE_ROOTS", str(tmp_path / "ok"))
        with pytest.raises(ExtensionError) as excinfo:
            await lifecycle.register_source(
                db_session,
                document={"local": {"path": str(tmp_path / "elsewhere")}, "ref": _REF_BRANCH},
                trust_tier="T2",
            )
        assert excinfo.value.code == "path-denied"

    async def test_production_descriptor_rejects_local_and_requires_https(self, db_session):
        with pytest.raises(ExtensionError) as excinfo:
            await lifecycle.register_source(
                db_session,
                document={"local": {"path": "/tmp/x"}, "url": "https://example.com/x"},
                trust_tier="T2",
            )
        assert excinfo.value.code == "invalid"


class TestSourceRows:
    async def test_git_source_sync_is_typed_not_ready(self, db_session):
        source = await _make_source(
            db_session,
            {"url": "https://git.example.com/ext/demo.git", "ref": _REF_BRANCH},
        )
        assert source.kind == "git"
        with pytest.raises(ExtensionError) as excinfo:
            await lifecycle.sync_source(db_session, source)
        assert excinfo.value.code == "not-ready"
        assert source.last_error is not None
        assert source.last_check_at is not None

    async def test_digest_tree_ignores_symlinks_and_is_stable(self, local_source_roots):
        bundle = _write_bundle(local_source_roots / "bundle-digest", _t3_manifest())
        (bundle / "docs").mkdir()
        (bundle / "docs" / "note.md").write_text("hello", encoding="utf-8")
        baseline = lifecycle.digest_tree(bundle)

        linked = local_source_roots / "linked.md"
        linked.write_text("evil", encoding="utf-8")
        (bundle / "docs" / "leak.md").symlink_to(linked)
        assert lifecycle.digest_tree(bundle) == baseline, "symlinks never contribute"

        (bundle / "docs" / "note.md").write_text("changed", encoding="utf-8")
        assert lifecycle.digest_tree(bundle) != baseline


class TestConsentEcho:
    def test_forged_echo_is_typed_invalid(self):
        manifest = _t3_manifest()
        echo = lifecycle.consent_echo(manifest, "a" * 64)
        assert lifecycle.verify_consent_echo(echo, manifest, "a" * 64) == echo
        with pytest.raises(ExtensionError) as excinfo:
            lifecycle.verify_consent_echo({**echo, "digest": "b" * 64}, manifest, "a" * 64)
        assert excinfo.value.code == "invalid"
        with pytest.raises(ExtensionError) as excinfo:
            lifecycle.verify_consent_echo({"digest": "a" * 64}, manifest, "a" * 64)
        assert excinfo.value.code == "invalid"


class TestBundledSurfacing:
    def test_bundled_rejections_surface_not_log_and_skip(self, tmp_path, monkeypatch):
        from app.extensions.registry import integration_registry

        broken = tmp_path / "broken_ext"
        broken.mkdir()
        (broken / "manifest.json").write_text('{"id": "nope"}', encoding="utf-8")
        monkeypatch.setattr(integration_registry, "_base_path", str(tmp_path))
        monkeypatch.setattr(integration_registry, "_rejected", {})
        integration_registry._load_manifests()
        rejected = lifecycle.registration_failures()
        assert "broken_ext" in rejected
        assert "registration:" in rejected["broken_ext"]
