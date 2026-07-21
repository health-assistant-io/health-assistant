"""
Tests for the HITL resume continuation feature (Tier 1).

Covers:
  * Pure unit tests for the resolution-summary helpers
    (`_hitl_resolution_summary`, `_hitl_resolved_brief`).
  * Endpoint guard behavior for POST /ai-assistance/sessions/{id}/resume
    using a mocked AIAssistanceService so no LLM/DB plumbing is required.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.main import app
from app.core.security import get_current_user
from app.models.enums import HitlTaskStatus
from app.schemas.user import TokenData
from app.ai.assistance.service import (
    _hitl_resolution_summary,
    _hitl_resolved_brief,
)


def _token():
    return TokenData(
        user_id=uuid4(),
        sub="test@example.com",
        role="USER",
        tenant_id=uuid4(),
    )


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


def test_resolution_summary_confirmed_and_dismissed():
    tasks = [
        {
            "task_type": "create_clinical_event",
            "title": "Pregnancy",
            "proposal_id": "aaa11111bbbb",
            "status": HitlTaskStatus.CONFIRMED,
            "resolved": {
                "final_payload": {"title": "Pregnancy", "type_slug": "pregnancy"},
                "result": {"event_id": "evt-123"},
            },
        },
        {
            "task_type": "add_medication",
            "title": "Metformin",
            "proposal_id": "bbb22222cccc",
            "status": HitlTaskStatus.DISMISSED,
            "resolved": {"at": "2026-06-20T12:00:00Z"},
        },
    ]
    out = _hitl_resolution_summary(tasks)
    assert "[HITL RESOLUTION FEEDBACK]" in out
    assert "1 confirmed, 1 dismissed" in out
    assert "create_clinical_event" in out
    assert "Pregnancy" in out
    assert "CONFIRMED" in out
    assert '"event_id": "evt-123"' in out
    assert "DISMISSED by the user" in out
    # Guidance should always be present.
    assert "Continue the conversation" in out


def test_resolution_summary_includes_failed_count():
    tasks = [
        {
            "task_type": "add_biomarker_to_examination",
            "title": "Glucose",
            "proposal_id": "ccc33333",
            "status": HitlTaskStatus.FAILED,
            "resolved": {"error": "validation refused"},
        },
    ]
    out = _hitl_resolution_summary(tasks)
    assert "1 failed" in out
    assert "FAILED (validation refused)" in out


def test_resolution_summary_trims_large_payload():
    """Only short identifying keys should be surfaced; arbitrary payload
    fields must NOT leak into the LLM context."""
    tasks = [
        {
            "task_type": "create_biomarker_definition",
            "title": "HbA1c",
            "proposal_id": "ddd44444",
            "status": HitlTaskStatus.CONFIRMED,
            "resolved": {
                "final_payload": {
                    "name": "HbA1c",
                    "slug": "hba1c",
                    "info": "x" * 5000,  # large field that should NOT leak
                    "secret_field": "should-not-leak",
                },
                "result": {"biomarker_id": "bio-9"},
            },
        },
    ]
    out = _hitl_resolution_summary(tasks)
    assert '"name": "HbA1c"' in out
    assert '"slug": "hba1c"' in out
    assert '"biomarker_id": "bio-9"' in out
    assert "should-not-leak" not in out
    assert "xxxxx" not in out


def test_resolved_brief_skips_pending():
    tasks = [
        {"task_type": "x", "title": "Pending One", "status": HitlTaskStatus.PROPOSED, "resolved": None},
        {
            "task_type": "create_clinical_event",
            "title": "Done",
            "status": HitlTaskStatus.CONFIRMED,
            "resolved": {"result": {"id": "abc-1234567890"}},
        },
        {"task_type": "add_medication", "title": "Nope", "status": HitlTaskStatus.DISMISSED, "resolved": {}},
    ]
    brief = _hitl_resolved_brief(tasks)
    assert brief is not None
    assert "Pending One" not in brief
    assert "Done" in brief
    assert "confirmed" in brief
    assert "(id=abc-1234)" in brief  # trimmed to 8 chars
    assert "dismissed" in brief


def test_resolved_brief_returns_none_when_nothing_resolved():
    tasks = [{"task_type": "x", "title": "y", "status": HitlTaskStatus.PROPOSED, "resolved": None}]
    assert _hitl_resolved_brief(tasks) is None
    assert _hitl_resolved_brief([]) is None


def test_resolution_summary_accepts_plain_string_payloads():
    """Backward-compat: tasks loaded from JSONB carry plain strings, not enum
    instances. The helpers must accept both interchangeably."""
    tasks = [
        {
            "task_type": "create_clinical_event",
            "title": "Plain",
            "proposal_id": "eee55555",
            "status": "confirmed",  # plain string, as stored in JSONB
            "resolved": {"result": {"id": "xyz-9876543210"}},
        },
    ]
    out = _hitl_resolution_summary(tasks)
    assert "1 confirmed" in out
    # Summary surfaces full result JSON (the brief is what trims ids to 8 chars).
    assert '"id": "xyz-9876543210"' in out
    assert _hitl_resolved_brief(tasks) is not None


def test_terminal_set_helper():
    """HitlTaskStatus.terminal() returns the statuses that are 'finished'."""
    terminal = HitlTaskStatus.terminal()
    assert HitlTaskStatus.CONFIRMED in terminal
    assert HitlTaskStatus.DISMISSED in terminal
    assert HitlTaskStatus.FAILED in terminal
    assert HitlTaskStatus.PROPOSED not in terminal


def test_resolution_summary_partial_resume_marks_unanswered():
    """When the user clicks Continue with some items still proposed, the
    summary must clearly mark them as 'NOT YET ANSWERED' and provide
    different LLM guidance (don't auto-repropose skipped items)."""
    tasks = [
        {
            "task_type": "create_clinical_event",
            "title": "Pregnancy",
            "proposal_id": "aaa11111",
            "status": HitlTaskStatus.CONFIRMED,
            "resolved": {"result": {"event_id": "evt-1"}},
        },
        {
            "task_type": "add_medication",
            "title": "Metformin",
            "proposal_id": "bbb22222",
            "status": HitlTaskStatus.PROPOSED,
            "resolved": None,
        },
    ]
    out = _hitl_resolution_summary(tasks)
    # Header includes the unanswered count.
    assert "1 left unanswered" in out
    # Unanswered item is clearly labeled.
    assert "NOT YET ANSWERED" in out
    # Guidance for partial resume tells the LLM not to repropose.
    assert "do NOT re-propose them automatically" in out


def test_resolution_summary_all_answered_uses_standard_guidance():
    """When all items are terminal, the standard guidance is used (no mention
    of 'unanswered')."""
    tasks = [
        {
            "task_type": "create_clinical_event",
            "title": "Done",
            "proposal_id": "aaa11111",
            "status": HitlTaskStatus.CONFIRMED,
            "resolved": {},
        },
    ]
    out = _hitl_resolution_summary(tasks)
    assert "unanswered" not in out.lower()
    assert "do NOT re-propose the same actions" in out


# ---------------------------------------------------------------------------
# Resume endpoint guard tests (mocked service)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _override_user():
    """Bypass real auth for every test in this module."""
    token = _token()
    app.dependency_overrides[get_current_user] = lambda: token
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_resume_endpoint_streams_content(async_client):
    """When the service yields sentinel chunks, the endpoint wraps each as
    `data: {"content": ...}` SSE frames.

    NOTE: `resume_after_hitl` is an async generator function (uses `yield`),
    so calling it returns the async generator directly — the endpoint must
    NOT `await` it. We mock it as a plain function returning the generator
    (NOT an AsyncMock) so the test exercises the real call shape and would
    catch an `await` regression.
    """
    async def _fake_resume(*args, **kwargs):
        for chunk in ["Hello ", "[TOOL_CALL_START] get_system_time", "world"]:
            yield chunk

    with patch(
        "app.api.v1.endpoints.ai_assistance.AIAssistanceService"
    ) as mock_cls:
        mock_instance = MagicMock()
        # Plain lambda (not AsyncMock) — mirrors the real async-generator
        # function: `service.resume_after_hitl(...)` returns the gen directly.
        mock_instance.resume_after_hitl = lambda **kw: _fake_resume(**kw)
        mock_cls.return_value = mock_instance

        session_id = uuid4()
        resp = await async_client.post(
            f"/api/v1/ai-assistance/sessions/{session_id}/resume",
            json={},
        )
        assert resp.status_code == 200
        body = resp.text
        assert 'data: {"content": "Hello "}' in body
        assert 'data: {"content": "world"' in body
        assert "[TOOL_CALL_START]" in body


@pytest.mark.asyncio
async def test_resume_endpoint_proceeds_with_pending_tasks(async_client):
    """Pending (unanswered) tasks must NOT block the resume — the user may
    have clicked 'Continue' to proceed with partial answers. The endpoint
    should stream normally; the summary handles pending items."""

    async def _fake_resume(*args, **kwargs):
        yield "Acknowledged your partial review."
        yield ""

    with patch(
        "app.api.v1.endpoints.ai_assistance.AIAssistanceService"
    ) as mock_cls:
        mock_instance = MagicMock()
        mock_instance.resume_after_hitl = lambda **kw: _fake_resume(**kw)
        mock_cls.return_value = mock_instance

        session_id = uuid4()
        resp = await async_client.post(
            f"/api/v1/ai-assistance/sessions/{session_id}/resume",
            json={},
        )
        assert resp.status_code == 200
        assert "Acknowledged your partial review" in resp.text
        # No error frame should be emitted.
        assert '"error"' not in resp.text


@pytest.mark.asyncio
async def test_resume_endpoint_passes_message_id_selector(async_client):
    """When `message_id` is supplied in the body, the endpoint must forward
    it to the service so the server picks the right task cluster."""

    captured = {}

    async def _fake_resume(*args, **kwargs):
        captured.update(kwargs)
        if False:  # pragma: no cover
            yield ""

    with patch(
        "app.api.v1.endpoints.ai_assistance.AIAssistanceService"
    ) as mock_cls:
        mock_instance = MagicMock()
        mock_instance.resume_after_hitl = lambda **kw: _fake_resume(**kw)
        mock_cls.return_value = mock_instance

        session_id = uuid4()
        msg_id = uuid4()
        resp = await async_client.post(
            f"/api/v1/ai-assistance/sessions/{session_id}/resume",
            json={"message_id": str(msg_id)},
        )
        assert resp.status_code == 200
        assert captured.get("message_id") == msg_id
        assert captured.get("session_id") == session_id


# ---------------------------------------------------------------------------
# /resolve audit-note differentiation (ask_user vs propose_*)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_audit_note_ask_user_says_answered_not_saved(async_client):
    """The audit note appended after /resolve must NOT say 'saved' for an
    ask_user task — those tasks perform no REST write; the user only
    answered questions. Saying 'saved' confused the LLM into thinking the
    card title WAS the saved item (it ignored the structured feedback).
    See dev/plans/ai-ask-user-questions-2026-07-21.md."""
    from app.api.v1.endpoints import ai_assistance as ep

    captured_messages: list = []

    fake_message = MagicMock()
    fake_message.tasks = [
        {
            "proposal_id": "p1",
            "task_type": "ask_user",
            "title": "Select biomarker to add to examination",
            "status": "proposed",
            "proposed_payload": {"summary": "Pick one", "questions": []},
            "resolved": None,
        }
    ]

    async def _no_op_update_tasks(msg, tasks):
        return None

    async def _capture_save(**kwargs):
        captured_messages.append(kwargs)

    with patch.object(ep, "ChatSessionService") as mock_cls:
        inst = mock_cls.return_value
        inst.find_message_by_proposal = AsyncMock(return_value=fake_message)
        inst.update_message_tasks = AsyncMock(side_effect=_no_op_update_tasks)
        inst.save_message = AsyncMock(side_effect=_capture_save)

        session_id = uuid4()
        resp = await async_client.post(
            f"/api/v1/ai-assistance/sessions/{session_id}/tasks/p1/resolve",
            json={"status": "confirmed", "final_payload": {"answers": {"q": "x"}}},
        )

    assert resp.status_code == 200, resp.text
    # Exactly one audit-note assistant message saved after the resolve.
    assert len(captured_messages) == 1
    text = captured_messages[0]["content"]["text"]
    assert "answered" in text.lower()
    assert "saved" not in text.lower(), (
        "ask_user audit note must not say 'saved' — nothing was written; the "
        "word 'saved' previously misled the LLM into thinking the card title "
        f"was the saved item. Got: {text!r}"
    )
    assert "Select biomarker to add to examination" in text


@pytest.mark.asyncio
async def test_resolve_audit_note_propose_says_saved(async_client):
    """For propose_* tasks (write actions), the audit note must KEEP the
    'Confirmed and saved' wording — those tasks really do save via the
    canonical REST endpoint."""
    from app.api.v1.endpoints import ai_assistance as ep

    captured_messages: list = []

    fake_message = MagicMock()
    fake_message.tasks = [
        {
            "proposal_id": "p1",
            "task_type": "create_clinical_event",
            "title": "Create Clinical Event: Pregnancy",
            "status": "proposed",
            "proposed_payload": {"title": "Pregnancy"},
            "resolved": None,
        }
    ]

    with patch.object(ep, "ChatSessionService") as mock_cls:
        inst = mock_cls.return_value
        inst.find_message_by_proposal = AsyncMock(return_value=fake_message)
        inst.update_message_tasks = AsyncMock(return_value=None)
        inst.save_message = AsyncMock(
            side_effect=lambda **kw: captured_messages.append(kw)
        )

        session_id = uuid4()
        resp = await async_client.post(
            f"/api/v1/ai-assistance/sessions/{session_id}/tasks/p1/resolve",
            json={"status": "confirmed"},
        )

    assert resp.status_code == 200
    assert len(captured_messages) == 1
    text = captured_messages[0]["content"]["text"]
    assert "Confirmed and saved" in text
    assert "Pregnancy" in text
