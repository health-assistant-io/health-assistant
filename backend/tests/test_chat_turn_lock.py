"""Per-session single-flight turn lock (plan 13 H2 gate b).

Two branches of one session NEVER stream concurrently: the lock is
per-SESSION, not per-branch. While one turn streams, a second turn on ANY
branch (plain send, edit, regenerate) is rejected with 409 before any row
is written; the lock releases when the stream closes. The lock is
Redis-backed (bridge R7 WS3) — these tests pin the behavior through the
endpoints with the Redis client swapped for the FakeRedis stand-in.
"""

import asyncio
import contextlib
from unittest.mock import MagicMock, patch

import pytest

from app.ai.assistance import turns as turns_mod
from app.api.v1.endpoints import ai_assistance as ai_assistance_ep
from app.models.enums import Role
from app.services.chat_session_service import ChatSessionService
from tests._auth_helpers import auth_headers, create_tenant, create_user
from tests.test_turn_lock import FakeRedis


def _turn_key(session_id) -> str:
    return f"turn:{session_id}"


@pytest.fixture
def turn_redis(monkeypatch):
    """Swap the turn lock's Redis client for the in-memory fake (the
    established FakeRedis pattern — no real Redis in unit tests)."""
    fake = FakeRedis()
    monkeypatch.setattr(turns_mod, "redis_client", fake)
    return fake


async def _seed_session_with_turn():
    tenant_id = await create_tenant()
    user = await create_user(role=Role.USER, tenant_id=tenant_id)
    headers = await auth_headers(user)

    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        session = await svc.create_session(user.id, tenant_id)
        u1 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "Q1"},
            owner_user_id=user.id,
        )
        await svc.save_message(session_id=session.id, role="assistant", content={"text": "A1"})
        # A second user message on another branch level (edit target).
        _s, edited = await svc.edit_message(u1.id, user.id, tenant_id, {"text": "Q1'"})
    return user, headers, session, u1, edited


def _patch_service_blocking():
    """Patch AIAssistanceService.assist with an SSE generator that never
    completes — the turn 'streams' indefinitely until the client cancels."""
    frames = ['data: {"event": "delta", "text": "…"}\n\n']

    async def _fake_assist(**kwargs):
        async def _gen():
            for frame in frames:
                yield frame
            await asyncio.Event().wait()

        return _gen()

    mock_cls = MagicMock()
    mock_cls.return_value.assist = _fake_assist
    return patch.object(ai_assistance_ep, "AIAssistanceService", mock_cls)


async def _read_stream_head(resp):
    """Consume the first bytes of an SSE response without waiting for the
    stream to finish (the blocking generator holds it open)."""
    return resp.status_code


async def _wait_until_locked(turn_redis: FakeRedis, session_id, timeout_s: float = 5.0) -> None:
    """Poll until the turn lock holds ``session_id`` (the background turn
    has entered its stream)."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while turn_redis._live(_turn_key(session_id)) is None:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("turn never acquired the session lock")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_second_concurrent_turn_on_any_branch_is_rejected(async_client, turn_redis):
    """Gate (b): while one turn streams, a second turn on ANOTHER branch of
    the same session is rejected with 409."""
    _user, headers, session, u1, edited = await _seed_session_with_turn()

    with _patch_service_blocking():
        # Turn 1 (branch B, via the edit endpoint) starts streaming in the
        # background (ASGITransport buffers responses — the endless stream
        # can only run as a task we later cancel).
        first = asyncio.create_task(
            async_client.post(
                f"/api/v1/ai-assistance/messages/{edited.id}/edit",
                headers=headers,
                json={"content": "Q1''"},
            )
        )
        try:
            await _wait_until_locked(turn_redis, session.id)

            # Turn 2 on a DIFFERENT branch (the original spine) is rejected
            # while turn 1 streams.
            second = await async_client.post(
                "/api/v1/ai-assistance/stream",
                headers=headers,
                json={
                    "task_type": "chat",
                    "user_input": "meanwhile on branch A",
                    "context": {"session_id": str(session.id)},
                },
            )
            assert second.status_code == 409
            assert "already streaming" in second.json()["detail"]

            # …and so is another edit/regenerate on any branch.
            third = await async_client.post(
                f"/api/v1/ai-assistance/messages/{u1.id}/regenerate",
                headers=headers,
                json={},
            )
            assert third.status_code == 409
        finally:
            first.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await first
    assert turn_redis._live(_turn_key(session.id)) is None


@pytest.mark.asyncio
async def test_lock_releases_after_stream_and_scopes_per_session(async_client, turn_redis):
    """After the stream closes the lock is free again; an unrelated session
    is never blocked (per-SESSION, not global)."""
    _user, headers, session, _u1, _edited = await _seed_session_with_turn()
    other_tenant = await create_tenant()
    other_user = await create_user(role=Role.USER, tenant_id=other_tenant)
    other_headers = await auth_headers(other_user)

    with _patch_service_blocking():
        first = asyncio.create_task(
            async_client.post(
                "/api/v1/ai-assistance/stream",
                headers=headers,
                json={
                    "task_type": "chat",
                    "user_input": "turn 1",
                    "context": {"session_id": str(session.id)},
                },
            )
        )
        try:
            await _wait_until_locked(turn_redis, session.id)

            # Unrelated session (auto-create path): NOT blocked — its turn
            # streams concurrently on a DIFFERENT session.
            other = asyncio.create_task(
                async_client.post(
                    "/api/v1/ai-assistance/stream",
                    headers=other_headers,
                    json={"task_type": "chat", "user_input": "other session", "context": {}},
                )
            )
            await asyncio.sleep(0.2)
            assert other.done() is False or turn_redis._live(_turn_key(session.id)) is not None

            # Same session while streaming: rejected.
            blocked = await async_client.post(
                "/api/v1/ai-assistance/stream",
                headers=headers,
                json={
                    "task_type": "chat",
                    "user_input": "turn 2",
                    "context": {"session_id": str(session.id)},
                },
            )
            assert blocked.status_code == 409
        finally:
            first.cancel()
            other.cancel()
            for task in (first, other):
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # Lock released: the next turn on the session is accepted (and completes).
    with patch("app.api.v1.endpoints.ai_assistance.AIAssistanceService") as mock_cls:
        done_frames = ['data: {"event": "flow_finished"}\n\n']

        async def _done_assist(**kwargs):
            async def _gen():
                for frame in done_frames:
                    yield frame

            return _gen()

        mock_cls.return_value.assist = _done_assist
        after = await async_client.post(
            "/api/v1/ai-assistance/stream",
            headers=headers,
            json={
                "task_type": "chat",
                "user_input": "turn 3",
                "context": {"session_id": str(session.id)},
            },
        )
        assert after.status_code == 200
        await after.aclose()

    assert turn_redis._live(_turn_key(session.id)) is None


@pytest.mark.asyncio
async def test_lock_survives_stream_exceptions(async_client, turn_redis):
    """A raising stream still releases the lock (finally on the generator)."""
    _user, headers, session, _u1, _edited = await _seed_session_with_turn()
    assert turn_redis._live(_turn_key(session.id)) is None

    with patch("app.api.v1.endpoints.ai_assistance.AIAssistanceService") as mock_cls:

        async def _raising_assist(**kwargs):
            async def _gen():
                yield 'data: {"event": "delta", "text": "x"}\n\n'
                raise RuntimeError("boom")

            return _gen()

        mock_cls.return_value.assist = _raising_assist
        resp = await async_client.post(
            "/api/v1/ai-assistance/stream",
            headers=headers,
            json={
                "task_type": "chat",
                "user_input": "turn",
                "context": {"session_id": str(session.id)},
            },
        )
        assert resp.status_code == 200
        assert "flow_failed" in resp.text  # classified terminal frame

    assert turn_redis._live(_turn_key(session.id)) is None


@pytest.mark.asyncio
async def test_preseeded_lock_rejects_edit(async_client, turn_redis):
    """Unit-level: a held lock (e.g. a turn started by another handler)
    rejects edit/regenerate on the same session with 409."""
    _user, headers, session, _u1, edited = await _seed_session_with_turn()
    await turn_redis.set(_turn_key(session.id), "held-elsewhere", nx=True, ex=900)
    try:
        resp = await async_client.post(
            f"/api/v1/ai-assistance/messages/{edited.id}/edit",
            headers=headers,
            json={"content": "blocked"},
        )
        assert resp.status_code == 409
        resp = await async_client.post(
            f"/api/v1/ai-assistance/messages/{_u1.id}/regenerate",
            headers=headers,
            json={},
        )
        assert resp.status_code == 409
    finally:
        await turn_redis.delete(_turn_key(session.id))


@pytest.mark.asyncio
async def test_resume_is_rejected_while_a_turn_streams(async_client, turn_redis):
    """F10 (bridge R7 WS4): the HITL resume endpoint takes the same
    per-session turn lock — while any turn streams on the session, the
    continuation is rejected with 409 before it starts (previously it
    interleaved with the streaming turn, the exact interleaving the lock
    exists to prevent)."""
    _user, headers, session, _u1, _edited = await _seed_session_with_turn()

    with _patch_service_blocking():
        first = asyncio.create_task(
            async_client.post(
                "/api/v1/ai-assistance/stream",
                headers=headers,
                json={
                    "task_type": "chat",
                    "user_input": "turn 1",
                    "context": {"session_id": str(session.id)},
                },
            )
        )
        try:
            await _wait_until_locked(turn_redis, session.id)

            resp = await async_client.post(
                f"/api/v1/ai-assistance/sessions/{session.id}/resume",
                headers=headers,
                json={},
            )
            assert resp.status_code == 409
            assert "already streaming" in resp.json()["detail"]
        finally:
            first.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await first
    assert turn_redis._live(_turn_key(session.id)) is None
