"""Human-in-the-loop (HITL) helpers + the resume continuation turn.

Extracted from ``AIAssistanceService`` (Phase 2). The five module-level helpers
are pure functions (no service/DB state) and are re-exported from
``app.ai.assistance.service`` for backward compatibility with tests.

``resume_after_hitl`` is the continuation turn driven after a user resolves
one or more HITL task cards. It performs the resume-specific setup (session
verification, resolution-summary build, summary persistence) then delegates
the reasoning loop to :func:`app.ai.agents.chat_agent.run_reasoning_loop`.

The ``chat_agent`` import is lazy (inside ``resume_after_hitl``) to avoid a
top-level cycle: ``chat_agent`` imports the two simple helpers below at module
load, while this module needs ``run_reasoning_loop`` only at call time.
"""
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import HitlTaskStatus

logger = logging.getLogger(__name__)


def _parse_hitl_proposal(observation: Any) -> Optional[Dict[str, Any]]:
    """Inspect a chatbot tool result for a human-in-the-loop proposal.

    Proposal tools return a JSON string (or dict) shaped like:
        {"__hitl__": True, "task": { ...full task payload... }}
    Returns the task dict when a proposal is detected, otherwise None.
    Never raises — malformed observations are treated as non-HITL.
    """
    parsed = observation
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (ValueError, TypeError):
            return None
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("__hitl__"):
        return None
    task = parsed.get("task") or parsed.get("proposal")
    if not isinstance(task, dict):
        return None
    return task


def _hitl_llm_feedback(task: Dict[str, Any]) -> str:
    """Concise note appended to the LLM history so the agent knows a proposal
    was emitted and that it must wait for human confirmation (no auto-retry)."""
    return (
        f"[HITL] A {task.get('task_type', 'action')} proposal has been rendered "
        f"as a review card for the user ({task.get('title', '')}). "
        f"The user must explicitly confirm or edit it before it takes effect. "
        f"Do NOT call the same proposal tool again for this request; continue "
        f"your explanation and wait for the user's response."
    )


def _hitl_resolution_summary(tasks: List[Dict[str, Any]]) -> str:
    """Build the structured outcomes message fed back to the agent when a HITL
    continuation turn is triggered. Reads status + final_payload + result +
    error from each resolved task. Returned text becomes the body of a synthetic
    user message that drives the continuation turn.

    Handles three cases per task:
      - CONFIRMED: surfaces trimmed draft + result ids
      - DISMISSED: notes the user rejected it
      - FAILED: surfaces the error
      - still PROPOSED: the user chose to continue WITHOUT answering (partial
        resume via the Continue button). Clearly labeled so the LLM knows the
        user hasn't acted on it yet.
    """
    lines = []
    confirmed = 0
    dismissed = 0
    failed = 0
    unanswered = 0
    for t in tasks:
        # Status may arrive as a HitlTaskStatus enum (fresh from a propose_*
        # tool) or as a plain string (loaded from JSONB). The comparisons
        # below use the enum form (status_raw); the plain-string form is only
        # needed in _hitl_resolved_brief, not here.
        status_raw = t.get("status", HitlTaskStatus.PROPOSED)
        resolved = t.get("resolved") or {}
        title = t.get("title") or t.get("task_type", "action")
        task_type = t.get("task_type", "action")
        proposal_id = t.get("proposal_id", "?")
        if status_raw == HitlTaskStatus.CONFIRMED:
            confirmed += 1
            parts = ["CONFIRMED"]
            final = resolved.get("final_payload")
            if isinstance(final, dict) and final:
                # Trim large payloads — surface only short identifying fields.
                keys = ("name", "title", "slug", "coding_system", "code")
                trimmed = {k: final[k] for k in keys if k in final}
                if trimmed:
                    parts.append(f"draft={json.dumps(trimmed, ensure_ascii=False)}")
            result = resolved.get("result")
            if isinstance(result, dict) and result:
                keys = ("id", "biomarker_id", "catalog_id", "event_id", "slug")
                trimmed = {k: result[k] for k in keys if k in result}
                if trimmed:
                    parts.append(f"result={json.dumps(trimmed, ensure_ascii=False)}")
            err = resolved.get("error")
            if err:
                parts.append(f"error={err}")
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): " + ", ".join(parts) + "."
            )
        elif status_raw == HitlTaskStatus.DISMISSED:
            dismissed += 1
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): DISMISSED by the user."
            )
        elif status_raw == HitlTaskStatus.FAILED:
            failed += 1
            err = resolved.get("error", "unknown error")
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): FAILED ({err})."
            )
        else:
            unanswered += 1
            lines.append(
                f"- [{task_type}] {title} ({proposal_id[:8]}): NOT YET ANSWERED "
                f"(the user continued without resolving this item)."
            )

    # Build header with counts.
    counts = []
    if confirmed:
        counts.append(f"{confirmed} confirmed")
    if dismissed:
        counts.append(f"{dismissed} dismissed")
    if failed:
        counts.append(f"{failed} failed")
    if unanswered:
        counts.append(f"{unanswered} left unanswered")
    counts_str = ", ".join(counts) if counts else "no items"

    header = (
        f"[HITL RESOLUTION FEEDBACK] The user has finished acting on your "
        f"proposals ({counts_str})."
    )

    # Guidance varies based on whether there are unanswered items.
    if unanswered:
        guidance = (
            "Continue the conversation based on these outcomes. For confirmed "
            "items, briefly acknowledge what was saved. For dismissed/failed "
            "items, ask how the user wants to proceed. For items left "
            "UNANSWERED, do NOT re-propose them automatically — the user chose "
            "to skip them. You may ask if they want to address them now, or "
            "simply move on. Do not parrot payload data back verbatim."
        )
    else:
        guidance = (
            "Continue the conversation based on these outcomes: "
            "(a) if all confirmed, briefly acknowledge what was saved and offer "
            "any natural follow-up; (b) if any were dismissed, ask the user how "
            "they'd like to proceed instead; (c) do NOT re-propose the same "
            "actions. Do not repeat the payload details back to the user verbatim."
        )
    return f"{header}\n" + "\n".join(lines) + f"\n{guidance}"


def _hitl_resolved_brief(tasks: List[Dict[str, Any]]) -> Optional[str]:
    """Compact one-line summary of resolved tasks on a past assistant message,
    injected into history reconstruction so the agent remembers prior HITL
    outcomes across user turns. Returns None if no tasks were resolved."""
    bits = []
    terminal = HitlTaskStatus.terminal()
    for t in tasks:
        status_raw = t.get("status")
        if status_raw not in terminal:
            continue
        # Normalize enum -> plain string for display.
        status = status_raw.value if isinstance(status_raw, HitlTaskStatus) else status_raw
        task_type = t.get("task_type", "action")
        title = t.get("title") or task_type
        result = (t.get("resolved") or {}).get("result") or {}
        rid = ""
        if isinstance(result, dict):
            for k in ("id", "biomarker_id", "catalog_id", "event_id", "slug"):
                if k in result:
                    rid = f" (id={str(result[k])[:8]})"
                    break
        bits.append(f"{task_type} '{title}': {status}{rid}")
    if not bits:
        return None
    return "; ".join(bits)


def _append_assistant_turn_to_history(msg: Any, history: list) -> None:
    """Reconstruct a past assistant message + its tool results into ``history``.

    OpenAI's chat API requires that every assistant message carrying
    ``tool_calls`` be followed by a ``ToolMessage`` for **each** ``tool_call_id``.
    The previous reconstruction only re-injected a ToolMessage for HITL-task
    turns and silently dropped normal tool results — so on the next turn the
    stored assistant ``tool_calls`` had no following tool responses and OpenAI
    rejected the request with ``BadRequestError: 400 ... tool_call_ids did not
    have response messages``.

    This replays the per-tool ``result`` (captured at tool-execution time and
    persisted on the assistant message's ``tool_calls`` JSONB) as one
    ``ToolMessage`` per ``tool_call_id``. The compact HITL outcome brief is
    folded into the LAST tool response so the agent still remembers what the
    user confirmed/dismissed on prior turns (and so we don't emit a synthetic
    tool_call_id, which some providers reject).
    """
    raw_calls = list(msg.tool_calls or [])
    t_calls: List[Dict[str, Any]] = []
    for idx, tc in enumerate(raw_calls):
        # Fall back to a unique synthetic id (per index) if none was stored —
        # the AIMessage.tool_calls and the ToolMessage response MUST share it.
        tc_id = tc.get("id") or f"call_{msg.id.hex[:8]}_{idx}"
        t_calls.append(
            {"name": tc.get("name", "tool"), "args": tc.get("args", {}), "id": tc_id}
        )

    history.append(
        AIMessage(content=(msg.content or {}).get("text"), tool_calls=t_calls)
    )

    # One ToolMessage per tool_call_id — satisfies OpenAI's contract and
    # replays the actual past observation so the model retains prior context.
    brief = _hitl_resolved_brief(list(msg.tasks or []))
    last_idx = len(t_calls) - 1
    for idx, tc in enumerate(t_calls):
        raw = raw_calls[idx] if idx < len(raw_calls) else {}
        result = raw.get("result")
        if result is None:
            result = "(no result stored)"
        if brief and idx == last_idx:
            result = f"{result}\n[HITL outcomes for this turn: {brief}]"
        history.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))


async def resume_after_hitl(
    db: AsyncSession,
    chat_session_service,
    ai_provider_service,
    max_iterations: int,
    session_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    message_id: Optional[UUID] = None,
):
    """Stream a continuation turn after the user has resolved one or more HITL
    task cards. Reads the resolved tasks from the target message's ``tasks``
    JSONB, builds a structured outcomes summary, persists it as a user message,
    then runs the shared reasoning loop (streaming).

    Guards:
      * Session must belong to (user_id, tenant_id).
      * Target message must exist and have tasks (graceful fallback otherwise).

    Yields the same SSE sentinel vocabulary as the streaming chat path.
    """
    # Lazy import to avoid the chat_agent <-> hitl top-level cycle.
    from sqlalchemy import select

    from app.ai.agents.chat_agent import (
        build_chat_tools,
        reconstruct_history,
        run_reasoning_loop,
        stream_loop_as_sse,
    )
    from app.ai.agents.prompts import build_resume_system_prompt
    from app.models.chat_model import ChatSession as _ChatSession

    # Verify ownership + pull patient_id for tool context.
    session_result = await db.execute(
        select(_ChatSession).where(
            _ChatSession.id == session_id,
            _ChatSession.user_id == user_id,
            _ChatSession.tenant_id == tenant_id,
        )
    )
    session = session_result.scalars().first()
    if not session:
        raise ValueError("Session not found or access denied.")

    target = await chat_session_service.find_resumable_message(
        session_id, user_id, tenant_id, message_id=message_id
    )

    if target and target.tasks:
        # Pending (unanswered) tasks do NOT hard-block the resume — the user
        # may have clicked "Continue" to proceed with partial answers. The
        # summary labels unanswered items as "NOT YET ANSWERED".
        terminal = HitlTaskStatus.terminal()
        pending = [
            t for t in target.tasks
            if isinstance(t, dict) and t.get("status") not in terminal
        ]
        if pending:
            logger.info(
                f"HITL resume: {len(pending)} task(s) still pending in "
                f"session {session_id} — proceeding with partial resume."
            )
        summary = _hitl_resolution_summary(target.tasks)
    else:
        # GRACEFUL FALLBACK: no task-bearing message (e.g. the proposing stream
        # was interrupted before the proactive-save fix, or an old session).
        # Don't error — run the continuation with a generic prompt.
        logger.warning(
            f"HITL resume: no task-bearing message found in session "
            f"{session_id}; falling back to generic continuation."
        )
        summary = (
            "[HITL RESOLUTION FEEDBACK] The user has resolved a proposed "
            "action from your previous turn. The outcome is recorded in "
            "the session history (look for the '✓ Confirmed and saved:' "
            "or dismissal note). Please acknowledge the outcome and "
            "offer any natural follow-up."
        )

    # Persist the summary as a user message so it reconstructs naturally
    # into LLM history on subsequent turns.
    await chat_session_service.save_message(
        session_id=session_id,
        role="user",
        content={"text": summary},
    )

    patient_id = str(session.patient_id) if session.patient_id else None
    # Resume turns don't carry exam context.
    tools = await build_chat_tools(
        db, tenant_id, patient_id, user_id, examination_id=None, label="resume"
    )

    llm = await ai_provider_service.get_llm(
        "chat", tenant_id=tenant_id, user_id=user_id
    )
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    system_prompt = build_resume_system_prompt()
    history = await reconstruct_history(
        chat_session_service, session_id, user_id, tenant_id, system_prompt, summary
    )

    loop = run_reasoning_loop(
        llm_with_tools,
        tools,
        history,
        max_iterations,
        streaming=True,
        chat_session_service=chat_session_service,
        session_id=session_id,
        log_label="AI Assistance (resume)",
    )
    async for chunk in stream_loop_as_sse(loop):
        yield chunk
