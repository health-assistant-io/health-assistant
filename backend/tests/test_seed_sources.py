"""The seed source service — registration, upload, sync pins, approval
lifecycle, revocation restore, adopt, retire (ADR 0056 Phases 3-4).

Pinned behaviors:

* registration validates kind/scope/priority bands (system 0-199, tenant
  200-999) and refuses the boot-only ``bundled`` kind; a tenant actor
  cannot create system-scope sources;
* cross-tenant ids are hidden 404s (``SOURCE_NOT_FOUND``), never 403s;
* uploads (zip + single item-file JSON) land as digest-bound pending
  versions; a re-upload of the same content returns the existing row;
  traversal entries and unknown filenames are typed refusals;
* sync registers a fetched digest as pending with its pins; a digest
  already registered is "nothing new"; a manifest whose scope/priority
  disagree with the source row is a typed refusal recorded in
  ``last_error`` — the pin stands and no version row appears;
* approval flips the digest only; apply refuses unapproved versions and
  advances the pointer; revocation re-applies the previous approved
  version and restores the items the revoked version had retired;
* adopt clears ``user_modified`` (optionally for one seed_key) and
  re-applies so the pack content finally wins;
* retire flips the source's items to ``retired`` (never deletes), keeps
  items another active source still provides, records the outcome as run
  items, and disables the source;
* every mutation writes an audit row.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.enums import (
    SeedItemAction,
    SeedItemState,
    SeedRunStatus,
    SeedSourceKind,
    SeedSourceScope,
    SeedVersionStatus,
)
from app.models.fhir.medication import MedicationCatalog
from app.models.seed_source_model import (
    SeedImportRunItem,
    SeedSource,
    SeedSourceVersion,
)
from app.models.user_model import Role
from app.services.seed import sources_service
from app.services.seed import transport as seed_transport
from app.services.seed.applier import SeedApplyError
from app.services.seed.sources_service import SeedSourceError
from app.services.seed.transport import SeedTransportError
from tests.seed_lane import provenance_for


def _admin(tenant_id=None):
    return SimpleNamespace(role=Role.SYSTEM_ADMIN, user_id=uuid.uuid4(), tenant_id=tenant_id)


async def _real_tenant() -> uuid.UUID:
    from tests._auth_helpers import create_tenant

    return await create_tenant()


def _tenant_admin(tenant_id):
    return SimpleNamespace(role=Role.ADMIN, user_id=uuid.uuid4(), tenant_id=tenant_id)


def med_item(key: str, name: str, **extra) -> dict:
    return {"seed_key": f"medication:{key}", "name": name, **extra}


def _zip_bytes(items_by_file, *, manifest_extra=None) -> bytes:
    manifest = {
        "id": "sources-test-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "CC0-1.0",
        "scope": "system",
        "priority": 100,
        "item_files": [],
    }
    manifest.update(manifest_extra or {})
    files: dict[str, bytes] = {}
    for filename, rows in (items_by_file or {}).items():
        content = json.dumps(
            {"metadata": {"version": "1.0.0"}, "items": rows},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        path = f"items/{filename}"
        files[path] = content
        manifest["item_files"].append({"path": path, "sha256": hashlib.sha256(content).hexdigest()})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "uploads"))
    return tmp_path


async def _upload(db, source, single_json_or_zip, *, actor=None, filename="medications.json"):
    return await sources_service.register_upload(
        db,
        source,
        content=single_json_or_zip,
        filename=filename,
        actor=actor or _admin(),
    )


async def _row_by_key(db, seed_key):
    return (
        await db.execute(select(MedicationCatalog).where(MedicationCatalog.seed_key == seed_key))
    ).scalar_one_or_none()


async def _cleanup(db, *, seed_keys=(), sources=()):
    """Remove this test's rows: provenance first, then entity rows, then the
    sources (versions + runs + deferred refs cascade)."""
    for key in seed_keys:
        row = await _row_by_key(db, key)
        if row is not None:
            prov = await provenance_for(db, "medication", row.id)
            if prov is not None:
                await db.delete(prov)
            await db.delete(row)
    await db.commit()
    for source in sources:
        fresh = await db.get(SeedSource, source.id)
        if fresh is not None:
            await db.delete(fresh)
    await db.commit()


# -- registration ---------------------------------------------------------------


async def test_create_source_validates_bands_and_kinds(db):
    tenant = await _real_tenant()
    with pytest.raises(SeedSourceError) as bundled:
        await sources_service.create_source(
            db, kind="bundled", priority=0, scope="system", actor=_admin()
        )
    assert bundled.value.code == "INVALID_KIND"
    with pytest.raises(SeedSourceError) as band:
        await sources_service.create_source(
            db,
            kind="git",
            priority=500,
            scope="system",
            actor=_admin(),
            uri="https://x.example.com/r",
        )
    assert band.value.code == "INVALID_PRIORITY"
    with pytest.raises(SeedSourceError) as tenant_band:
        await sources_service.create_source(
            db,
            kind="git",
            priority=150,
            scope="tenant",
            actor=_admin(tenant_id=tenant),
            uri="https://x.example.com/r",
            tenant_id=tenant,
        )
    assert tenant_band.value.code == "INVALID_PRIORITY"
    await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant})
    await db.commit()


async def test_create_source_tenant_actor_cannot_create_system(db):
    with pytest.raises(SeedSourceError) as excinfo:
        await sources_service.create_source(
            db, kind="file", priority=100, scope="system", actor=_tenant_admin(uuid.uuid4())
        )
    assert excinfo.value.code == "FORBIDDEN_SCOPE"


async def test_create_source_git_uri_is_validated(db):
    with pytest.raises(SeedTransportError) as excinfo:
        await sources_service.create_source(
            db, kind="git", priority=100, scope="system", actor=_admin(), uri="file:///tmp/repo"
        )
    assert excinfo.value.code in ("URI_INVALID", "NET_DENIED")


async def test_hidden_404_cross_tenant(db):
    tenant_a = await _real_tenant()
    tenant_b = await _real_tenant()
    source = await sources_service.create_source(
        db,
        kind="file",
        priority=250,
        scope="tenant",
        actor=_admin(tenant_id=tenant_a),
        tenant_id=tenant_a,
    )
    with pytest.raises(SeedSourceError) as excinfo:
        await sources_service.get_source_for_actor(db, str(source.id), _tenant_admin(tenant_b))
    assert excinfo.value.code == "SOURCE_NOT_FOUND"
    seen = await sources_service.get_source_for_actor(db, str(source.id), _tenant_admin(tenant_a))
    assert seen.id == source.id
    listed = await sources_service.list_sources_for_actor(db, _tenant_admin(tenant_a))
    assert any(s["id"] == str(source.id) for s in listed)
    listed_b = await sources_service.list_sources_for_actor(db, _tenant_admin(tenant_b))
    assert not any(s["id"] == str(source.id) for s in listed_b)
    await db.delete(source)
    await db.commit()
    await db.execute(
        text("DELETE FROM tenants WHERE id IN (:a, :b)"), {"a": tenant_a, "b": tenant_b}
    )
    await db.commit()


# -- upload ---------------------------------------------------------------------


async def test_upload_zip_creates_pending_version_and_is_digest_idempotent(db, cache_env):
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=_admin()
    )
    version = await _upload(
        db,
        source,
        _zip_bytes({"medications.json": [med_item("upload-med", "Upload Med")]}),
        filename="pack.zip",
    )
    assert version.status is SeedVersionStatus.PENDING
    again = await _upload(
        db,
        source,
        _zip_bytes({"medications.json": [med_item("upload-med", "Upload Med")]}),
        filename="pack.zip",
    )
    assert again.id == version.id, "the same content resolves to the same digest row"
    versions = (
        (
            await db.execute(
                select(SeedSourceVersion).where(SeedSourceVersion.source_id == source.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(versions) == 1
    await _cleanup(db, seed_keys=("medication:upload-med",), sources=(source,))


async def test_upload_single_json(db, cache_env):
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=_admin()
    )
    content = json.dumps(
        {"metadata": {"version": "1.0.0"}, "items": [med_item("upload-json-med", "JSON Med")]}
    ).encode("utf-8")
    version = await _upload(db, source, content)
    assert version.status is SeedVersionStatus.PENDING
    assert version.manifest["priority"] == 100, "the single-JSON manifest carries the source row"
    await _cleanup(db, seed_keys=(), sources=(source,))


async def test_upload_traversal_zip_is_typed(db, cache_env):
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=_admin()
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"id": "x"}))
        zf.writestr("items/../evil.json", b"{}")
    with pytest.raises(SeedTransportError) as excinfo:
        await _upload(db, source, buffer.getvalue(), filename="pack.zip")
    assert excinfo.value.code == "ARCHIVE_PATH_TRAVERSAL"
    await db.delete(source)
    await db.commit()


async def test_upload_unknown_filename_is_typed(db, cache_env):
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=_admin()
    )
    content = json.dumps({"metadata": {"version": "1.0.0"}, "items": [med_item("x", "X")]}).encode(
        "utf-8"
    )
    with pytest.raises(SeedSourceError) as excinfo:
        await _upload(db, source, content, filename="evil.json")
    assert excinfo.value.code == "PACK_INVALID"
    await db.delete(source)
    await db.commit()


async def test_upload_priority_mismatch_zip_is_refused(db, cache_env):
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=_admin()
    )
    content = _zip_bytes(
        {"medications.json": [med_item("mismatch-med", "M")]}, manifest_extra={"priority": 5}
    )
    with pytest.raises(SeedSourceError) as excinfo:
        await _upload(db, source, content, filename="pack.zip")
    assert excinfo.value.code == "PRIORITY_MISMATCH"
    await db.delete(source)
    await db.commit()


# -- sync ------------------------------------------------------------------------


async def test_sync_file_source_is_a_noop(db, cache_env):
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=_admin()
    )
    result = await sources_service.sync_source(db, source, actor=_admin())
    assert result["status"] == "up_to_date"
    assert "upload" in result["note"]
    assert source.last_check_at is not None
    await db.delete(source)
    await db.commit()


class _FakeFetched:
    """A transport.FetchedPack stand-in built from manifest+files."""

    def __init__(self, manifest, files, *, etag=None):
        self.manifest = manifest
        self.files = files
        self.etag = etag
        self.final_url = None
        self.commit_sha = None

    @property
    def is_not_modified(self):
        return not self.files and not self.manifest


def _raw_files(pack):
    """Rebuild raw item-file bytes from a parsed pack (the manifest pins them)."""
    out = {}
    for path, parsed_file in pack.files.items():
        payload = {"metadata": {"version": "1.0.0"}}
        payload.update(parsed_file.collections)
        out[path] = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return out


async def test_sync_registers_pending_version_with_pins(db, cache_env, monkeypatch):
    pack = make_pack_for_sync({"medications.json": [med_item("sync-med", "Sync Med")]})
    fetched = _FakeFetched(pack.manifest, _raw_files(pack), etag='"etag-1"')
    fetched.commit_sha = "f" * 40

    async def fake_fetch(source, **kwargs):
        return fetched

    monkeypatch.setattr(seed_transport, "fetch_pack", fake_fetch)
    source = await sources_service.create_source(
        db,
        kind="registry",
        priority=100,
        scope="system",
        actor=_admin(),
        uri="https://reg.example.com/p.zip",
    )
    result = await sources_service.sync_source(db, source, actor=_admin())
    assert result["status"] == "registered"
    version = result["versions"][0]
    assert version["status"] == "pending"
    assert version["commit_sha"] == "f" * 40
    assert version["etag"] == '"etag-1"'
    assert source.last_error is None and source.last_check_at is not None

    same = await sources_service.sync_source(db, source, actor=_admin())
    assert same["status"] == "up_to_date", "the digest already registered — nothing new"
    count = len(
        (
            await db.execute(
                select(SeedSourceVersion).where(SeedSourceVersion.source_id == source.id)
            )
        )
        .scalars()
        .all()
    )
    assert count == 1
    await db.delete(source)
    await db.commit()


def make_pack_for_sync(items_by_file):
    """A validated in-memory pack (the lane helper, without persisting)."""
    from tests.seed_lane import make_pack

    return make_pack(items_by_file)


async def test_sync_priority_mismatch_keeps_the_pin(db, cache_env, monkeypatch):
    from tests.seed_lane import make_pack

    pack = make_pack(
        {"medications.json": [med_item("mismatch-sync-med", "M")]},
        version="1.0.0",
        priority=55,
    )

    async def fake_fetch(source, **kwargs):
        return _FakeFetched(pack.manifest, _raw_files(pack))

    monkeypatch.setattr(seed_transport, "fetch_pack", fake_fetch)
    source = await sources_service.create_source(
        db,
        kind="registry",
        priority=100,
        scope="system",
        actor=_admin(),
        uri="https://reg.example.com/p.zip",
    )
    with pytest.raises(SeedSourceError) as excinfo:
        await sources_service.sync_source(db, source, actor=_admin())
    assert excinfo.value.code == "PRIORITY_MISMATCH"
    assert source.last_error and "priority" in source.last_error
    versions = (
        (
            await db.execute(
                select(SeedSourceVersion).where(SeedSourceVersion.source_id == source.id)
            )
        )
        .scalars()
        .all()
    )
    assert versions == [], "a refused pack never registers"
    assert "reg.example.com" in source.uri, "the pin stands"
    await db.delete(source)
    await db.commit()


async def test_sync_secret_missing_is_typed(db, cache_env):
    source = await sources_service.create_source(
        db,
        kind="registry",
        priority=100,
        scope="system",
        actor=_admin(),
        uri="https://reg.example.com/p.zip",
        auth_secret_ref="does-not-exist",
    )
    with pytest.raises(SeedSourceError) as excinfo:
        await sources_service.sync_source(db, source, actor=_admin())
    assert excinfo.value.code == "SECRET_MISSING"
    assert source.last_error and "does-not-exist" in source.last_error
    await db.delete(source)
    await db.commit()


# -- lifecycle: approve / apply / revoke ------------------------------------------


async def test_approve_apply_pointer_and_audit(db, cache_env):
    admin = _admin()
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin
    )
    content = json.dumps(
        {"metadata": {"version": "1.0.0"}, "items": [med_item("apply-med", "Apply Med")]}
    ).encode("utf-8")
    version = await _upload(db, source, content)
    with pytest.raises(SeedSourceError) as refused:
        await sources_service.apply_version(db, source, version, actor=admin)
    assert refused.value.code == "VERSION_NOT_APPROVED"

    await sources_service.approve_version(db, version, actor=admin)
    assert version.status is SeedVersionStatus.APPROVED
    assert str(version.approved_by) == str(admin.user_id)
    await db.commit()

    outcome = await sources_service.apply_version(db, source, version, actor=admin)
    status = next(iter(outcome.statuses.values()))
    assert status is SeedRunStatus.OK, outcome.plan_docs

    row = await _row_by_key(db, "medication:apply-med")
    assert row is not None
    await db.refresh(source)
    assert str(source.current_version_id) == str(version.id)

    audit = (
        await db.execute(
            text(
                "SELECT action FROM audit_events WHERE resource_id = :rid "
                "AND action IN ('seed.version.approve', 'seed.source.upload')"
            ),
            {"rid": version.id},
        )
    ).first()
    assert audit is not None
    await _cleanup(db, seed_keys=("medication:apply-med",), sources=(source,))


async def test_revoke_restores_previous_version_items(db, cache_env):
    admin = _admin()
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin
    )

    async def upload(items, label):
        content = _zip_bytes(
            {"medications.json": items}, manifest_extra={"version": f"1.0.{label}"}
        )
        return await _upload(db, source, content, filename="pack.zip")

    v1 = await upload([med_item("rev-med-a", "Rev Med A"), med_item("rev-med-b", "Rev Med B")], 0)
    v2 = await upload([med_item("rev-med-a", "Rev Med A")], 1)
    assert v1.digest != v2.digest
    await sources_service.approve_version(db, v1, actor=admin)
    await db.commit()
    await sources_service.apply_version(db, source, v1, actor=admin)
    await sources_service.approve_version(db, v2, actor=admin)
    await db.commit()
    outcome = await sources_service.apply_version(db, source, v2, actor=admin)
    assert next(iter(outcome.statuses.values())) is SeedRunStatus.OK

    row_b = await _row_by_key(db, "medication:rev-med-b")
    prov_b = await provenance_for(db, "medication", row_b.id)
    assert prov_b.state is SeedItemState.RETIRED, "v2 dropped B — it retired"

    await db.commit()
    result = await sources_service.revoke_version(db, v2, actor=admin)
    assert result["restored_version_id"] == str(v1.id)
    await db.refresh(prov_b)
    await db.refresh(source)
    assert prov_b.state is SeedItemState.ACTIVE, "revocation re-applied v1 and restored B"
    await db.refresh(source)
    assert str(source.current_version_id) == str(v1.id)

    audit = (
        await db.execute(
            text(
                "SELECT action FROM audit_events WHERE resource_id = :rid "
                "AND action = 'seed.version.revoke'"
            ),
            {"rid": v2.id},
        )
    ).first()
    assert audit is not None
    await _cleanup(
        db, seed_keys=("medication:rev-med-a", "medication:rev-med-b"), sources=(source,)
    )


async def test_approve_refuses_non_pending(db, cache_env):
    admin = _admin()
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin
    )
    content = json.dumps(
        {"metadata": {"version": "1.0.0"}, "items": [med_item("rej-med", "Rej Med")]}
    ).encode("utf-8")
    version = await _upload(db, source, content)
    await sources_service.reject_version(db, version, actor=admin, reason="not vetted")
    assert version.status is SeedVersionStatus.REJECTED and version.reason == "not vetted"
    with pytest.raises(SeedSourceError) as excinfo:
        await sources_service.approve_version(db, version, actor=admin)
    assert excinfo.value.code == "VERSION_NOT_PENDING"
    await db.delete(version)
    await db.delete(source)
    await db.commit()


# -- adopt ------------------------------------------------------------------------


async def test_adopt_clears_freeze_and_applies(db, cache_env):
    admin = _admin()
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin
    )

    async def upload(name):
        content = _zip_bytes(
            {"medications.json": [med_item("adopt-med", name)]},
            manifest_extra={"version": "1.0.1" if name != "Adopt Med V1" else "1.0.0"},
        )
        return await _upload(db, source, content, filename="pack.zip")

    v1 = await upload("Adopt Med V1")
    await sources_service.approve_version(db, v1, actor=admin)
    await db.commit()
    await sources_service.apply_version(db, source, v1, actor=admin)
    row = await _row_by_key(db, "medication:adopt-med")
    prov = await provenance_for(db, "medication", row.id)
    prov.user_modified = True
    await db.commit()

    v2 = await upload("Adopt Med V2")
    await sources_service.approve_version(db, v2, actor=admin)
    await db.commit()
    plan = await sources_service.plan_version(db, source, v2)
    entry = next(e for e in plan.entries if e.seed_key == "medication:adopt-med")
    assert entry.action is SeedItemAction.SKIP and entry.update_available, "the freeze plans a skip"
    await sources_service.apply_version(db, source, v2, actor=admin)
    await db.refresh(row)
    assert row.name == "Adopt Med V1", "the freeze holds until adoption"
    await db.commit()

    result = await sources_service.adopt_updates(db, source, actor=admin)
    assert result["cleared"] == 1
    await db.refresh(row)
    assert row.name == "Adopt Med V2", "adopting re-applies and the pack content wins"
    await db.refresh(prov)
    assert prov.user_modified is False
    await db.refresh(source)
    assert str(source.current_version_id) == str(v2.id)
    await _cleanup(db, seed_keys=("medication:adopt-med",), sources=(source,))


async def test_adopt_apply_failure_is_audited_once_and_typed(db, cache_env, monkeypatch):
    """A failing re-apply after the freeze-clears commit: exactly one
    ``seed.source.adopt`` audit row (outcome ``error`` with the failure)
    and a typed ``ADOPT_APPLY_FAILED`` — the clears never persist silently
    un-audited."""
    admin = _admin()
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin
    )
    content = json.dumps(
        {"metadata": {"version": "1.0.0"}, "items": [med_item("adopt-fail-med", "Adopt Fail Med")]}
    ).encode("utf-8")
    version = await _upload(db, source, content)
    await sources_service.approve_version(db, version, actor=admin)
    await db.commit()
    await sources_service.apply_version(db, source, version, actor=admin)
    row = await _row_by_key(db, "medication:adopt-fail-med")
    prov = await provenance_for(db, "medication", row.id)
    prov.user_modified = True
    await db.commit()

    class _BoomApplier:
        def apply(self, applications):
            raise SeedApplyError("APPLY_TEST_BOOM", "the re-apply exploded")

    monkeypatch.setattr(sources_service, "SeedPackApplier", _BoomApplier)
    with pytest.raises(SeedSourceError) as excinfo:
        await sources_service.adopt_updates(db, source, actor=admin)
    assert excinfo.value.code == "ADOPT_APPLY_FAILED"

    audits = (
        await db.execute(
            text(
                "SELECT outcome, new_value FROM audit_events "
                "WHERE action = 'seed.source.adopt' AND resource_id = :rid"
            ),
            {"rid": source.id},
        )
    ).all()
    assert len(audits) == 1, "the adoption is audited exactly once"
    assert audits[0][0] == "error"
    assert "APPLY_TEST_BOOM" in str(audits[0][1].get("error"))
    await db.refresh(prov)
    assert prov.user_modified is False, "the committed clears stand after the failure"

    await _cleanup(db, seed_keys=("medication:adopt-fail-med",), sources=(source,))


async def test_retire_holds_the_apply_lane_advisory_lock(db, cache_env):
    """Retire takes the same advisory key a concurrent apply holds — while
    an apply lane lock is held elsewhere, retire waits."""
    import asyncio

    from app.core.database import engine
    from app.services.seed.applier import _lock_key

    admin = _admin()
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin, uri="upload://lock"
    )
    key = _lock_key("system")
    async with engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        task = asyncio.create_task(sources_service.retire_source(db, source, actor=admin))
        for _ in range(20):
            await asyncio.sleep(0.05)
            if task.done():
                break
        assert not task.done(), "retire waits while the apply lane's lock is held elsewhere"
        await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        result = await task
    assert result["retired"] == 0 and source.enabled is False
    await db.delete(source)
    await db.commit()


# -- retire ------------------------------------------------------------------------


async def test_retire_keeps_still_provided_items_and_disables(db, cache_env):
    admin = _admin()
    source_a = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin, uri="upload://a"
    )
    source_b = await sources_service.create_source(
        db, kind="file", priority=101, scope="system", actor=admin, uri="upload://b"
    )

    async def upload(source, name):
        content = _zip_bytes(
            {"medications.json": [med_item("retire-med", name)]},
            manifest_extra={
                "version": "1.0.1" if name != "From A" else "1.0.0",
                "priority": int(source.priority),
            },
        )
        return await _upload(db, source, content, filename="pack.zip")

    va = await upload(source_a, "From A")
    vb = await upload(source_b, "From B")
    await sources_service.approve_version(db, va, actor=admin)
    await db.commit()
    await sources_service.apply_version(db, source_a, va, actor=admin)
    await sources_service.approve_version(db, vb, actor=admin)
    await db.commit()
    await sources_service.apply_version(db, source_b, vb, actor=admin)

    result = await sources_service.retire_source(db, source_a, actor=admin)
    assert result["kept"] == 1 and result["retired"] == 0, "B still provides the key"
    assert source_a.enabled is False
    row = await _row_by_key(db, "medication:retire-med")
    prov = await provenance_for(db, "medication", row.id)
    assert prov.state is SeedItemState.ACTIVE

    await sources_service.set_enabled(db, source_b, False, actor=admin)
    result = await sources_service.retire_source(db, source_b, actor=admin)
    assert result["retired"] == 1, "nothing provides the key anymore — it retires"
    await db.refresh(prov)
    assert prov.state is SeedItemState.RETIRED
    await db.refresh(row)
    assert row.id is not None, "retire never deletes the row"

    run_items = (
        (
            await db.execute(
                select(SeedImportRunItem).where(
                    SeedImportRunItem.seed_key == "medication:retire-med",
                    SeedImportRunItem.action == SeedItemAction.RETIRE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert run_items, "the retirement outcome is recorded as run items"
    await _cleanup(db, seed_keys=("medication:retire-med",), sources=(source_a, source_b))


async def test_reapply_door_retries_a_partial_apply(db, cache_env):
    """A PARTIAL apply keeps the pointer and surfaces the item errors; the
    explicit re-apply door (the same apply endpoint) records a fresh run
    that retries exactly the failed items."""
    admin = _admin()
    source = await sources_service.create_source(
        db, kind="file", priority=100, scope="system", actor=admin
    )
    content = _zip_bytes(
        {
            "medications.json": [
                med_item("partial-ok-med", "Partial OK Med"),
                {"seed_key": "medication:partial-bad-med"},
            ]
        },
        manifest_extra={"version": "9.0.0"},
    )
    version = await _upload(db, source, content, filename="pack.zip")
    await sources_service.approve_version(db, version, actor=admin)
    await db.commit()
    outcome = await sources_service.apply_version(db, source, version, actor=admin)
    status = next(iter(outcome.statuses.values()))
    assert status is SeedRunStatus.PARTIAL, outcome.plan_docs
    await db.refresh(source)
    assert str(source.current_version_id) == str(version.id), "PARTIAL keeps the pointer"

    outcome2 = await sources_service.apply_version(db, source, version, actor=admin)
    assert next(iter(outcome2.statuses.values())) is SeedRunStatus.PARTIAL
    async with AsyncSessionLocal() as count_db:
        from sqlalchemy import func
        from sqlalchemy import select as _select

        from app.models.seed_source_model import SeedImportRun

        runs = (
            await count_db.execute(
                _select(func.count())
                .select_from(SeedImportRun)
                .where(
                    SeedImportRun.source_version_id == version.id,
                    SeedImportRun.mode == "apply",
                )
            )
        ).scalar_one()
    assert runs >= 2, "each re-apply records its own run"

    await _cleanup(
        db, seed_keys=("medication:partial-ok-med", "medication:partial-bad-med"), sources=(source,)
    )


# -- bundled lane guards -----------------------------------------------------------


async def test_bundled_source_refuses_lifecycle(db, cache_env):
    source = SeedSource(
        kind=SeedSourceKind.BUNDLED,
        uri="test://bundled-guard",
        priority=0,
        scope=SeedSourceScope.SYSTEM,
        enabled=True,
    )
    db.add(source)
    await db.commit()
    try:
        with pytest.raises(SeedSourceError):
            await sources_service.sync_source(db, source, actor=_admin())
        with pytest.raises(SeedSourceError):
            await sources_service.set_enabled(db, source, False, actor=_admin())
        with pytest.raises(SeedSourceError):
            await sources_service.retire_source(db, source, actor=_admin())
    finally:
        await db.delete(source)
        await db.commit()


async def test_registry_source_refuses_upload(db, cache_env):
    source = await sources_service.create_source(
        db,
        kind="registry",
        priority=100,
        scope="system",
        actor=_admin(),
        uri="https://reg.example.com/p.zip",
    )
    with pytest.raises(SeedSourceError) as excinfo:
        await _upload(db, source, b"{}", filename="medications.json")
    assert excinfo.value.code == "INVALID_KIND"
    await db.delete(source)
    await db.commit()
