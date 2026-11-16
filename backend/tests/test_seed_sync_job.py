"""The seed-source sync job (ADR 0056 Phase 3 — startup never fetches).

Pinned behaviors:

* the beat entry runs every 6 hours with a per-task time-limit override
  (large packs must outlive the 15-minute default task cap);
* the cycle visits only ``git``/``registry`` sources (``file`` sources are
  upload-driven, ``bundled`` applies at boot);
* a source whose fetch fails records ``last_error`` and keeps every pin —
  the cycle continues with the remaining sources;
* a successful fetch registers the digest as a **pending** version (sync
  never applies — approval still gates the apply);
* logs/metrics carry ids, kinds and counts — no pack content, no PHI.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import SeedSourceKind, SeedVersionStatus
from app.models.seed_source_model import SeedSource, SeedSourceVersion
from app.services.seed import transport as seed_transport
from app.workers.celery_app import celery_app


def test_beat_entry_is_six_hours_with_time_limit_override():
    entry = celery_app.conf.beat_schedule.get("sync-seed-sources-every-6h")
    assert entry is not None
    assert entry["task"] == "app.workers.tasks.sync_seed_sources"
    assert entry["schedule"] == 21600.0
    task = celery_app.tasks["app.workers.tasks.sync_seed_sources"]
    assert task.soft_time_limit == 1500
    assert task.time_limit == 1800


async def _run_task():
    from app.workers.tasks import sync_seed_sources

    await asyncio.to_thread(sync_seed_sources.run)


async def _source_by_uri(db, uri: str) -> SeedSource | None:
    return (await db.execute(select(SeedSource).where(SeedSource.uri == uri))).scalar_one_or_none()


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def test_task_visits_git_registry_only_and_records_failures(db):
    git_source = SeedSource(
        kind=SeedSourceKind.GIT,
        uri="https://git.invalid.example.com/seeds.git",
        ref_spec="main",
        priority=100,
        enabled=True,
    )
    file_source = SeedSource(
        kind=SeedSourceKind.FILE,
        uri="upload://sync-job-file",
        priority=100,
        enabled=True,
    )
    db.add_all([git_source, file_source])
    await db.commit()
    try:
        await _run_task()
        await db.refresh(git_source)
        await db.refresh(file_source)
        assert git_source.last_error, "the unreachable git fetch is recorded in last_error"
        assert git_source.last_check_at is not None
        assert git_source.ref_spec == "main", "a failed sync keeps the pin"
        versions = (
            (
                await db.execute(
                    select(SeedSourceVersion).where(SeedSourceVersion.source_id == git_source.id)
                )
            )
            .scalars()
            .all()
        )
        assert versions == [], "a failed fetch registers nothing"
        assert file_source.last_error is None, "file sources are upload-driven, never fetched"
    finally:
        for source in (git_source, file_source):
            fresh = await db.get(SeedSource, source.id)
            if fresh is not None:
                await db.delete(fresh)
        await db.commit()


async def test_task_registers_pending_versions(db, monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "uploads"))

    class _FakeFetched:
        def __init__(self, manifest, files):
            self.manifest = manifest
            self.files = files
            self.etag = None
            self.final_url = None
            self.commit_sha = None

        @property
        def is_not_modified(self):
            return not self.files and not self.manifest

    from tests.seed_lane import make_pack

    pack = make_pack(
        {"medications.json": [{"seed_key": "medication:sync-job-med", "name": "Sync Job Med"}]}
    )

    def _raw_files(parsed):
        out = {}
        for path, parsed_file in parsed.files.items():
            import json as _json

            payload = {"metadata": {"version": "1.0.0"}}
            payload.update(parsed_file.collections)
            out[path] = _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return out

    async def fake_fetch(source, **kwargs):
        return _FakeFetched(pack.manifest, _raw_files(pack))

    monkeypatch.setattr(seed_transport, "fetch_pack", fake_fetch)
    source = SeedSource(
        kind=SeedSourceKind.REGISTRY,
        uri="https://reg.example.com/sync-job.zip",
        priority=100,
        enabled=True,
    )
    db.add(source)
    await db.commit()
    try:
        await _run_task()
        await db.refresh(source)
        assert source.last_error is None
        version = (
            await db.execute(
                select(SeedSourceVersion).where(SeedSourceVersion.source_id == source.id)
            )
        ).scalar_one()
        assert version.status is SeedVersionStatus.PENDING, "sync registers; approval applies"
        assert source.last_check_at is not None
    finally:
        fresh = await db.get(SeedSource, source.id)
        if fresh is not None:
            await db.delete(fresh)
        await db.commit()
