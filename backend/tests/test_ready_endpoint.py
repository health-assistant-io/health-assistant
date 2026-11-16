"""`/ready` readiness probe negative tests (plan 64 §1.4 / ADR-0062 v7 §4).

Hard checks (any failure ⇒ 503): boot (lifespan) complete, DB reachable and
at the release's Alembic head, **required** object storage. Soft signals
(Redis, Celery workers, queue backlog) are reported but never flip the
status — the last two tests pin that.
"""

from __future__ import annotations

import httpx
import pytest

import app.api.readiness as readiness
from app.main import app
from app.storage.health import STATUS_UNAVAILABLE, StorageHealth


async def _get(client: httpx.AsyncClient) -> httpx.Response:
    return await client.get("/ready")


@pytest.fixture
def boot_complete():
    """The global app has no lifespan under ASGITransport; pin the flag and
    restore whatever the fixture's caller leaves behind."""
    app.state.boot_complete = True
    yield
    app.state.boot_complete = False


@pytest.fixture
def at_head(monkeypatch):
    """DB reachable at the Alembic head (the real stamped head)."""

    async def _at_head() -> str:
        return readiness.head_revision()

    monkeypatch.setattr(readiness, "db_revision", _at_head)


@pytest.mark.anyio
async def test_ready_is_503_before_boot_completes(async_client, at_head):
    # No lifespan: pin the flag down (set at app creation; deterministic
    # even if another test in the session left it up).
    app.state.boot_complete = False
    response = await _get(async_client)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not-ready"
    assert body["checks"]["boot"]["ok"] is False


async def _get(client: httpx.AsyncClient) -> httpx.Response:
    return await client.get("/ready")


@pytest.mark.anyio
async def test_ready_is_200_when_boot_db_and_storage_are_green(
    async_client, boot_complete, at_head
):
    response = await _get(async_client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["boot"]["ok"] is True
    assert body["checks"]["db"]["ok"] is True
    assert body["checks"]["db"]["revision"]
    # Soft signals ride the payload but never gate (redis may be up or down
    # in the test env — either way the status stays "ready").
    assert "redis" in body["soft"]


@pytest.mark.anyio
async def test_ready_is_503_when_db_is_down(async_client, boot_complete, monkeypatch):
    async def _db_down() -> None:
        return None

    monkeypatch.setattr(readiness, "db_revision", _db_down)
    response = await _get(async_client)
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["db"]["ok"] is False
    assert body["checks"]["db"]["revision"] is None


@pytest.mark.anyio
async def test_ready_is_503_when_db_is_behind_migration_head(
    async_client, boot_complete, monkeypatch
):
    async def _behind_head() -> str:
        return "0001_baseline"

    monkeypatch.setattr(readiness, "db_revision", _behind_head)
    response = await _get(async_client)
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["db"]["ok"] is False
    assert body["checks"]["db"]["revision"] == "0001_baseline"
    assert "migration head" in body["checks"]["db"]["detail"]


@pytest.mark.anyio
async def test_ready_is_503_when_required_storage_is_down(
    async_client, boot_complete, at_head
):
    class _RequiredProbe:
        required = True

        def snapshot(self) -> StorageHealth:
            return StorageHealth(
                backend="s3", status=STATUS_UNAVAILABLE, required=True, detail="Boom"
            )

    app.state.storage_health = _RequiredProbe()
    try:
        response = await _get(async_client)
    finally:
        app.state.storage_health = None
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["storage"]["ok"] is False
    assert body["checks"]["storage"]["required"] is True


@pytest.mark.anyio
async def test_optional_storage_degradation_never_flips_readiness(
    async_client, boot_complete, at_head
):
    """A degraded/unavailable OPTIONAL provider is a soft signal (plan 64
    §1.4) — /ready stays 200 and says so in the payload."""

    class _OptionalProbe:
        required = False

        def snapshot(self) -> StorageHealth:
            return StorageHealth(
                backend="s3", status=STATUS_UNAVAILABLE, required=False, detail="Boom"
            )

    app.state.storage_health = _OptionalProbe()
    try:
        response = await _get(async_client)
    finally:
        app.state.storage_health = None
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["storage"]["ok"] is True
    assert body["checks"]["storage"]["status"] == STATUS_UNAVAILABLE


@pytest.mark.anyio
async def test_redis_being_down_never_flips_readiness(
    async_client, boot_complete, at_head, monkeypatch
):
    """Redis is a SOFT signal: rate limits/jti store degrade, readiness does
    not (plan 64 §1.4)."""

    class _DownRedis:
        def ping(self):
            raise ConnectionError("redis down")

        def llen(self, _queue):
            raise ConnectionError("redis down")

    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "redis_client", _DownRedis())
    readiness._soft_cache.update(at=0.0, workers="unknown", queue_backlog=None)
    response = await _get(async_client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["soft"]["redis"] == "not_available"


@pytest.mark.anyio
async def test_workers_offline_never_flips_readiness(
    async_client, boot_complete, at_head, monkeypatch
):
    monkeypatch.setattr(readiness, "_probe_workers", lambda: "no-reply")
    readiness._soft_cache.update(at=0.0, workers="unknown", queue_backlog=None)
    response = await _get(async_client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["soft"]["workers"] == "no-reply"


def test_head_revision_matches_the_bundled_chain():
    """The readiness gate compares against the release's own head (read from
    the migration files, never from the database)."""
    assert readiness.head_revision()
