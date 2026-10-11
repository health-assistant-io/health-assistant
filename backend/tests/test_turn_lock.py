"""Redis-backed chat turn lock (bridge R7 hardening WS3) — FakeRedis unit
tests: single-flight conflict, TTL-bounded leak classes, owner-token
compare-and-delete release, degrade-open on Redis errors. No real Redis is
touched (the established FakeRedis pattern — see test_audit_a2_rate_limit)."""

import logging
import uuid

import pytest

from app.ai.assistance import turns
from app.ai.assistance.turns import TURN_LOCK_MESSAGE, TurnConflictError


class _ListHandler(logging.Handler):
    """Collect records emitted on ``turns.logger`` directly — the app's
    import-time logging setup (root handler surgery in
    ``app.core.logging_setup``) makes pytest's ``caplog`` unreliable for
    async tests, so the warning assertions attach where the record is
    emitted."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def turns_warnings():
    """Capture ``turns`` warnings via a dedicated handler.

    The session ``run_migrations`` fixture triggers alembic's
    ``fileConfig(..., disable_existing_loggers=True)``, which silences
    loggers created at collection time — ``caplog`` therefore never sees
    ``turns`` records (the ``_capture_instance_state_logs`` pattern in
    test_demo_hygiene_h7 is the precedent). The handler attaches to the
    emitting logger with the ``disabled`` flag cleared for the capture
    window, which is immune to that.
    """
    handler = _ListHandler()
    was_disabled = turns.logger.disabled
    turns.logger.addHandler(handler)
    turns.logger.disabled = False
    yield handler
    turns.logger.removeHandler(handler)
    turns.logger.disabled = was_disabled


class FakeRedis:
    """Deterministic in-memory stand-in for the async redis client.

    Implements the command surface the turn lock uses — ``SET ... NX EX``
    with a movable clock (lazy TTL expiry on read), ``get``, ``delete`` —
    plus an ``eval`` that interprets the module's compare-and-delete
    release script (the fake has no Lua engine; the semantics it fakes are
    exactly `get == ARGV[1] then del`).
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expires_at: dict[str, float] = {}
        self.now = 1000.0
        self.fail = False

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _live(self, key: str) -> str | None:
        exp = self.expires_at.get(key)
        if exp is not None and self.now >= exp:
            self.store.pop(key, None)
            self.expires_at.pop(key, None)
            return None
        return self.store.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if self.fail:
            raise RuntimeError("redis down")
        if nx and self._live(key) is not None:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires_at[key] = self.now + ex
        return True

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("redis down")
        return self._live(key)

    async def delete(self, key: str) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.store.pop(key, None)
        self.expires_at.pop(key, None)

    async def eval(self, script: str, numkeys: int, *keys_and_args) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        key, token = keys_and_args[0], keys_and_args[1]
        if self._live(key) == token:
            self.store.pop(key, None)
            self.expires_at.pop(key, None)
            return 1
        return 0


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(turns, "redis_client", fake)
    return fake


async def test_first_acquire_locks_and_second_conflicts(fake_redis):
    session = uuid.uuid4()
    token = await turns.acquire_turn(session)
    assert token is not None
    assert fake_redis.store == {f"turn:{session}": token}
    with pytest.raises(TurnConflictError) as exc:
        await turns.acquire_turn(session)
    assert str(exc.value) == TURN_LOCK_MESSAGE


async def test_locks_are_per_session(fake_redis):
    session_a, session_b = uuid.uuid4(), uuid.uuid4()
    token_a = await turns.acquire_turn(session_a)
    token_b = await turns.acquire_turn(session_b)
    assert token_a is not None and token_b is not None
    assert token_a != token_b


async def test_ttl_expiry_frees_the_lock(fake_redis):
    session = uuid.uuid4()
    await turns.acquire_turn(session)
    fake_redis.advance(turns.TURN_LOCK_TTL_SECONDS)
    fresh = await turns.acquire_turn(session)
    assert fresh is not None
    with pytest.raises(TurnConflictError):
        await turns.acquire_turn(session)


async def test_release_with_wrong_token_is_a_noop(fake_redis):
    session = uuid.uuid4()
    token = await turns.acquire_turn(session)
    await turns.release_turn(session, "not-the-owner")
    assert fake_redis._live(f"turn:{session}") == token
    with pytest.raises(TurnConflictError):
        await turns.acquire_turn(session)


async def test_release_with_owner_token_frees_the_lock(fake_redis):
    session = uuid.uuid4()
    token = await turns.acquire_turn(session)
    await turns.release_turn(session, token)
    assert fake_redis._live(f"turn:{session}") is None
    fresh = await turns.acquire_turn(session)
    assert fresh is not None and fresh != token


async def test_late_finally_of_superseded_turn_cannot_unlock_newer_turn(fake_redis):
    """F2/F3 edge: a turn's key expires, a newer turn re-locks, then the
    old generator's late ``finally`` releases with the stale token."""
    session = uuid.uuid4()
    stale = await turns.acquire_turn(session)
    fake_redis.advance(turns.TURN_LOCK_TTL_SECONDS)
    fresh = await turns.acquire_turn(session)
    await turns.release_turn(session, stale)
    assert fake_redis._live(f"turn:{session}") == fresh
    with pytest.raises(TurnConflictError):
        await turns.acquire_turn(session)


async def test_acquire_degrades_open_when_redis_down(fake_redis, turns_warnings):
    fake_redis.fail = True
    token = await turns.acquire_turn(uuid.uuid4())
    assert token is None
    assert any("degraded open" in r.getMessage() for r in turns_warnings.records)


async def test_release_degrades_open_when_redis_down(fake_redis, turns_warnings):
    session = uuid.uuid4()
    fake_redis.fail = True
    await turns.release_turn(session, "owner-token")
    assert any("degraded open" in r.getMessage() for r in turns_warnings.records)


async def test_degraded_acquire_releases_as_a_noop(fake_redis):
    fake_redis.fail = True
    token = await turns.acquire_turn(uuid.uuid4())
    assert token is None
    await turns.release_turn(uuid.uuid4(), token)
    assert fake_redis.store == {}


async def test_acquire_works_again_after_redis_returns(fake_redis):
    fake_redis.fail = True
    assert await turns.acquire_turn(uuid.uuid4()) is None
    fake_redis.fail = False
    session = uuid.uuid4()
    token = await turns.acquire_turn(session)
    assert token is not None
    with pytest.raises(TurnConflictError):
        await turns.acquire_turn(session)


async def test_release_callable_is_injectable(fake_redis, monkeypatch):
    """Docstring pin: the Lua release runs through a module-level callable
    that tests can swap for a fake compare-and-delete."""
    calls = []

    async def fake_release(key: str, token: str) -> int:
        calls.append((key, token))
        return 1

    monkeypatch.setattr(turns, "_compare_and_delete", fake_release)
    session = uuid.uuid4()
    token = await turns.acquire_turn(session)
    await turns.release_turn(session, token)
    assert calls == [(f"turn:{session}", token)]
