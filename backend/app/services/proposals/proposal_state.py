"""The one HITL proposal state machine (plan 5 W1/W4).

All proposal status changes go through this module. It owns:

* the closed set of legal transitions (``PROPOSED → APPROVED → APPLIED``, plus
  the terminal exits ``REJECTED`` / ``FAILED`` / ``EXPIRED`` / ``SUPERSEDED``);
* typed errors for illegal transitions and genuine conflicts — callers map
  ``IllegalProposalTransition`` to 422/400 and ``ProposalConflict`` to 409, and
  never write a status column directly;
* idempotency: re-resolving a row to the state it already holds returns the
  stored outcome (200), while a *different* decision on a terminal row is a
  conflict (409) — the ADR-0015 "idempotent resolve, conflict = rebase-or-
  reject" rule;
* the audit trace (``resolution_trace``): who resolved what when, appended to a
  history list so a re-resolve never erases the first decision.

The wire card vocabulary (``HitlTaskStatus``: proposed/confirmed/failed/
dismissed) is mapped here — the DB vocabulary is deliberately richer and this
is the only place the two meet.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any
from uuid import UUID

from sqlalchemy.orm.attributes import flag_modified

from app.models.enums import HitlTaskStatus, ProposalStatus

#: Every legal move. Terminal states have no outgoing edges; ``APPROVED`` is
#: the transient state while a server-side apply is in flight, so an apply
#: crash leaves a row a retry can pick up instead of a silent half-write.
ALLOWED_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PROPOSED: frozenset(
        {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.APPLIED,
            ProposalStatus.FAILED,
            ProposalStatus.EXPIRED,
            ProposalStatus.SUPERSEDED,
        }
    ),
    ProposalStatus.APPROVED: frozenset({ProposalStatus.APPLIED, ProposalStatus.FAILED}),
    ProposalStatus.APPLIED: frozenset(),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.FAILED: frozenset(),
    ProposalStatus.EXPIRED: frozenset(),
    ProposalStatus.SUPERSEDED: frozenset(),
}

#: A repeat resolve that asks for the same outcome is a success (200), not a
#: conflict — this is the ADR-0015 idempotency floor. ``expired`` accepts a
#: late reject/dismiss as the same "discarded without applying" outcome.
IDEMPOTENT_TARGETS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.APPLIED: frozenset({ProposalStatus.APPLIED}),
    ProposalStatus.REJECTED: frozenset({ProposalStatus.REJECTED}),
    ProposalStatus.FAILED: frozenset({ProposalStatus.FAILED}),
    ProposalStatus.EXPIRED: frozenset({ProposalStatus.EXPIRED, ProposalStatus.REJECTED}),
    ProposalStatus.SUPERSEDED: frozenset({ProposalStatus.SUPERSEDED}),
}

#: Wire card status ⇄ DB status (one direction each, explicit).
_CARD_TO_PROPOSAL: dict[HitlTaskStatus, ProposalStatus] = {
    HitlTaskStatus.PROPOSED: ProposalStatus.PROPOSED,
    HitlTaskStatus.CONFIRMED: ProposalStatus.APPLIED,
    HitlTaskStatus.DISMISSED: ProposalStatus.REJECTED,
    HitlTaskStatus.FAILED: ProposalStatus.FAILED,
}
_PROPOSAL_TO_CARD: dict[ProposalStatus, HitlTaskStatus] = {
    ProposalStatus.PROPOSED: HitlTaskStatus.PROPOSED,
    ProposalStatus.APPROVED: HitlTaskStatus.CONFIRMED,
    ProposalStatus.APPLIED: HitlTaskStatus.CONFIRMED,
    ProposalStatus.REJECTED: HitlTaskStatus.DISMISSED,
    ProposalStatus.FAILED: HitlTaskStatus.FAILED,
    ProposalStatus.EXPIRED: HitlTaskStatus.DISMISSED,
    ProposalStatus.SUPERSEDED: HitlTaskStatus.DISMISSED,
}


class ProposalTransitionError(ValueError):
    """Base class for state-machine rejections."""


class IllegalProposalTransition(ProposalTransitionError):
    """The requested move is not an edge of the state machine."""

    def __init__(self, current: ProposalStatus, target: ProposalStatus, *, reason: str = ""):
        self.current = current
        self.target = target
        detail = f"illegal proposal transition {current.value!r} → {target.value!r}"
        if reason:
            detail = f"{detail}: {reason}"
        super().__init__(detail)


class ProposalConflict(ProposalTransitionError):
    """The row is terminal with a different outcome (or superseded)."""

    def __init__(self, current: ProposalStatus, target: ProposalStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"proposal is already {current.value!r}; {target.value!r} conflicts with the "
            f"recorded outcome"
        )


def assert_transition(current: ProposalStatus, target: ProposalStatus) -> None:
    """Raise unless ``current → target`` is a legal edge."""
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise IllegalProposalTransition(current, target)


def is_idempotent(current: ProposalStatus, target: ProposalStatus) -> bool:
    """True when re-applying ``target`` to ``current`` is a 200 no-op."""
    return target in IDEMPOTENT_TARGETS.get(current, frozenset())


def resolve_transition(current: ProposalStatus, target: ProposalStatus) -> bool:
    """Classify a resolve request.

    Returns ``True`` when it is an idempotent repeat (caller returns the
    stored outcome), ``False`` when it is a legal move. Raises
    :class:`ProposalConflict` for a conflicting terminal decision and
    :class:`IllegalProposalTransition` for a move the machine does not allow.
    """
    if is_idempotent(current, target):
        return True
    if current in ProposalStatus.terminal():
        raise ProposalConflict(current, target)
    assert_transition(current, target)
    return False


def card_status(status: ProposalStatus) -> HitlTaskStatus:
    """DB status → ADR-0015 wire card status."""
    return _PROPOSAL_TO_CARD[status]


def proposal_status(card: HitlTaskStatus | str) -> ProposalStatus:
    """ADR-0015 wire card status → DB status."""
    return _CARD_TO_PROPOSAL[HitlTaskStatus(card)]


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def transition(
    row: Any,
    target: ProposalStatus,
    *,
    action: str,
    actor_id: UUID | None = None,
    note: str | None = None,
    final_payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    superseded_by: UUID | None = None,
    now: _dt.datetime | None = None,
) -> None:
    """Apply one legal transition in place and append the audit trace.

    The caller owns the commit. Illegal moves raise typed errors; there is no
    "best effort" path that writes a status without a trace entry.
    """
    current = row.status
    if isinstance(current, str):
        current = ProposalStatus(current)
    if isinstance(target, str):
        target = ProposalStatus(target)
    assert_transition(current, target)

    stamp = (now or _now()).isoformat()
    trace = dict(row.resolution_trace or {})
    history = list(trace.get("history") or [])
    history.append(
        {
            "from": current.value,
            "to": target.value,
            "at": stamp,
            "by": str(actor_id) if actor_id is not None else None,
            "action": action,
        }
    )
    trace.update(
        {
            "history": history,
            "action": action,
            "resolved_by": str(actor_id) if actor_id is not None else trace.get("resolved_by"),
            "note": note if note is not None else trace.get("note"),
            "result": result if result is not None else trace.get("result"),
            "error": error,
            "at": stamp,
        }
    )
    row.resolution_trace = trace
    flag_modified(row, "resolution_trace")

    row.status = target
    if final_payload is not None:
        row.final_payload = final_payload
        flag_modified(row, "final_payload")
    if superseded_by is not None:
        row.superseded_by = superseded_by
    if target == ProposalStatus.APPLIED:
        row.applied_at = now or _now()
    if target in ProposalStatus.terminal():
        row.resolved_at = now or _now()


__all__ = [
    "ALLOWED_TRANSITIONS",
    "IDEMPOTENT_TARGETS",
    "IllegalProposalTransition",
    "ProposalConflict",
    "ProposalTransitionError",
    "assert_transition",
    "card_status",
    "is_idempotent",
    "proposal_status",
    "resolve_transition",
    "transition",
]
