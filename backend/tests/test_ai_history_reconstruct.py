"""Regression tests for chat history reconstruction.

Guards against the OpenAI ``BadRequestError: 400 ... tool_call_ids did not have
response messages`` failure. When a session continues, past assistant messages
that carried ``tool_calls`` MUST be followed by one ``ToolMessage`` per
``tool_call_id`` in the reconstructed history — otherwise the next ``astream``
call sends a malformed conversation and the provider rejects it.
"""
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from app.ai.assistance.service import _append_assistant_turn_to_history


def _msg(*, text="hi", tool_calls=None, tasks=None, mid=None):
    return SimpleNamespace(
        content={"text": text},
        tool_calls=tool_calls or [],
        tasks=tasks or [],
        id=mid or uuid4(),
    )


class TestAppendAssistantTurnToHistory:
    def test_message_without_tool_calls_appends_only_ai_message(self):
        history = []
        _append_assistant_turn_to_history(_msg(text="hello"), history)
        assert len(history) == 1
        assert isinstance(history[0], AIMessage)
        assert history[0].content == "hello"
        assert history[0].tool_calls == []

    def test_every_tool_call_id_gets_a_tool_message(self):
        # The core contract: one ToolMessage per tool_call_id, no gaps.
        history = []
        tc = [
            {"id": "call_a", "name": "get_patient_summary", "args": {},
             "result": '{"name": "Alice"}'},
            {"id": "call_b", "name": "get_recent_biomarkers", "args": {"limit": 3},
             "result": '[{"slug": "glucose"}]'},
        ]
        _append_assistant_turn_to_history(_msg(tool_calls=tc), history)

        assert isinstance(history[0], AIMessage)
        assert [c["id"] for c in history[0].tool_calls] == ["call_a", "call_b"]
        # Exactly one ToolMessage per tool_call_id, in order, immediately after.
        tool_responses = history[1:]
        assert len(tool_responses) == 2
        assert all(isinstance(m, ToolMessage) for m in tool_responses)
        assert [m.tool_call_id for m in tool_responses] == ["call_a", "call_b"]
        # The stored result is replayed verbatim.
        assert '{"name": "Alice"}' in tool_responses[0].content
        assert '[{"slug": "glucose"}]' in tool_responses[1].content

    def test_missing_result_falls_back_to_placeholder(self):
        # Older DB rows may have tool_calls without a stored `result`. The
        # ToolMessage must still be emitted (placeholder content) so the
        # tool_call_id contract holds.
        history = []
        tc = [{"id": "call_x", "name": "some_tool", "args": {}}]  # no "result"
        _append_assistant_turn_to_history(_msg(tool_calls=tc), history)
        tm = history[1]
        assert isinstance(tm, ToolMessage)
        assert tm.tool_call_id == "call_x"
        assert "no result stored" in tm.content

    def test_synthetic_id_is_unique_per_call_when_ids_absent(self):
        # If tool_call_ids weren't persisted, the helper synthesises unique
        # ones (per index) so two calls on the same message don't collide.
        history = []
        mid = uuid4()
        tc = [
            {"name": "t1", "args": {}, "result": "r1"},
            {"name": "t2", "args": {}, "result": "r2"},
        ]
        _append_assistant_turn_to_history(_msg(tool_calls=tc, mid=mid), history)
        ai_ids = [c["id"] for c in history[0].tool_calls]
        tm_ids = [m.tool_call_id for m in history[1:]]
        # AIMessage tool_call ids and ToolMessage ids must match exactly.
        assert ai_ids == tm_ids
        assert len(set(ai_ids)) == 2  # unique

    def test_hitl_brief_folded_into_last_tool_response(self):
        # The HITL outcome summary must ride on an existing tool_call_id
        # (the last one), not on a synthetic id (which providers reject).
        from app.models.enums import HitlTaskStatus

        history = []
        tc = [
            {"id": "call_data", "name": "get_recent_biomarkers", "args": {},
             "result": "[]"},
            {"id": "call_propose", "name": "propose_add_medication", "args": {},
             "result": "Proposal prepared. Awaiting user review."},
        ]
        tasks = [
            {
                "task_type": "add_medication",
                "title": "Add Aspirin",
                "status": HitlTaskStatus.CONFIRMED,
                "resolved": {"result": {"id": "abc12345"}},
            }
        ]
        _append_assistant_turn_to_history(
            _msg(tool_calls=tc, tasks=tasks), history
        )

        tool_responses = history[1:]
        assert len(tool_responses) == 2
        # Only the LAST response carries the brief.
        assert "HITL outcomes" not in tool_responses[0].content
        assert "HITL outcomes" in tool_responses[1].content
        assert "add_medication" in tool_responses[1].content
        assert "confirmed" in tool_responses[1].content
        # The original result text is preserved alongside the brief.
        assert "Proposal prepared" in tool_responses[1].content
