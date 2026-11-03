"""Shared HITL task-card resolution (family contract, ADR-0015 Class-B).

Extracted from the ``/ai-assistance`` resolve endpoint (2026-10-11) so the
health-assistant bridge can record resolutions through the integration
identity with the exact same semantics: the client commits any proposed
write through the canonical REST endpoint BEFORE resolving — this service
only records the outcome into the message's task JSONB (audit + agent
awareness); it never performs the write itself.
"""

import datetime as _dt
from uuid import UUID

from app.models.enums import HitlTaskStatus


class HitlResolutionError(ValueError):
    """Not-found / invalid proposal — maps to HTTP 404/400 upstream."""


class HitlResolutionConflict(Exception):
    """The proposal was already resolved — maps to HTTP 409 upstream."""


async def resolve_chat_task(
    chat_service,
    session_id: UUID,
    proposal_id: str,
    status: str,
    final_payload: dict | None = None,
    result: str | None = None,
    error: str | None = None,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    """Record a CONFIRMED/DISMISSED resolution on a proposal card and append
    the agent-awareness note. Returns the updated task dict.

    Raises:
        HitlResolutionError: proposal/message/session not found, or an
            invalid status transition target.
        HitlResolutionConflict: the proposal was already resolved.
    """
    if status not in (HitlTaskStatus.CONFIRMED, HitlTaskStatus.DISMISSED):
        raise HitlResolutionError("status must be 'confirmed' or 'dismissed'")

    message = await chat_service.find_message_by_proposal(
        session_id, proposal_id, user_id, tenant_id
    )
    if not message:
        raise HitlResolutionError("Task proposal not found or access denied.")

    tasks = list(message.tasks or [])
    task_idx = next(
        (
            i
            for i, t in enumerate(tasks)
            if isinstance(t, dict) and t.get("proposal_id") == proposal_id
        ),
        None,
    )
    if task_idx is None:
        raise HitlResolutionError("Task proposal not found.")

    task = tasks[task_idx]
    prior_status = task.get("status")
    if prior_status not in (None, HitlTaskStatus.PROPOSED):
        raise HitlResolutionConflict(f"Task already {prior_status}.")

    now = _dt.datetime.now(_dt.UTC).isoformat()
    task["status"] = status
    task["resolved"] = {
        "confirmed_by": str(user_id),
        "final_payload": final_payload,
        "result": result,
        "error": error,
        "at": now,
    }
    tasks[task_idx] = task
    await chat_service.update_message_tasks(message, tasks)

    # Agent awareness: append a concise assistant note so the next chat turn
    # knows the proposed action was carried out (or dismissed) by the user.
    # ``ask_user`` tasks are read-only — the audit note says "answered", not
    # "saved" (nothing was saved).
    task_type = task.get("task_type")
    if status == HitlTaskStatus.CONFIRMED:
        payload = task.get("proposed_payload") or {}
        label = payload.get("title") or task.get("title") or task.get("task_type")
        if task_type == "ask_user":
            note_text = f"✓ The user answered: {label}."
        else:
            note_text = f"✓ Confirmed and saved: {label}."
        await chat_service.save_message(
            session_id=session_id,
            role="assistant",
            content={"text": note_text},
        )
    elif status == HitlTaskStatus.DISMISSED:
        await chat_service.save_message(
            session_id=session_id,
            role="assistant",
            content={
                "text": "The proposed draft was dismissed. Let me know how you'd like to proceed."
            },
        )

    return task
