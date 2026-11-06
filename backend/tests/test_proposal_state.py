"""The one HITL proposal state machine (plan 5 W1/W4).

Pins the full transition matrix (every legal edge, every illegal move typed),
the idempotent-resolve rule, the conflict rule, and the audit trace.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.enums import HitlTaskStatus, ProposalSource, ProposalStatus
from app.models.proposal import Proposal
from app.services.proposals.proposal_state import (
    ALLOWED_TRANSITIONS,
    IllegalProposalTransition,
    ProposalConflict,
    assert_transition,
    card_status,
    is_idempotent,
    proposal_status,
    resolve_transition,
    transition,
)

ALL_STATES = list(ProposalStatus)


def _row(status: ProposalStatus = ProposalStatus.PROPOSED) -> Proposal:
    return Proposal(
        id=uuid4(),
        tenant_id=uuid4(),
        source=ProposalSource.CHAT,
        kind_id=uuid4(),
        status=status,
        payload={},
        payload_schema_version=1,
        resolution_trace={},
    )


@pytest.mark.parametrize("current", ALL_STATES)
@pytest.mark.parametrize("target", ALL_STATES)
def test_transition_matrix_is_exactly_the_declared_edges(current, target):
    if target in ALLOWED_TRANSITIONS[current]:
        assert_transition(current, target)
    else:
        with pytest.raises(IllegalProposalTransition) as excinfo:
            assert_transition(current, target)
        assert excinfo.value.current == current
        assert excinfo.value.target == target


def test_every_non_terminal_state_has_an_exit():
    for state in ALL_STATES:
        if state in ProposalStatus.terminal():
            assert ALLOWED_TRANSITIONS[state] == frozenset()
        else:
            assert ALLOWED_TRANSITIONS[state], f"{state} is a dead end"


def test_proposed_can_reject_expire_or_apply_directly():
    """Chat resolves record a client-committed write as ``applied`` without an
    ``approved`` hop; extension applies go through ``approved``."""
    assert ProposalStatus.APPLIED in ALLOWED_TRANSITIONS[ProposalStatus.PROPOSED]
    assert ProposalStatus.APPROVED in ALLOWED_TRANSITIONS[ProposalStatus.PROPOSED]
    assert ProposalStatus.REJECTED in ALLOWED_TRANSITIONS[ProposalStatus.PROPOSED]
    assert ProposalStatus.EXPIRED in ALLOWED_TRANSITIONS[ProposalStatus.PROPOSED]
    assert ProposalStatus.SUPERSEDED in ALLOWED_TRANSITIONS[ProposalStatus.PROPOSED]


@pytest.mark.parametrize(
    "current,target",
    [
        (ProposalStatus.APPLIED, ProposalStatus.APPLIED),
        (ProposalStatus.REJECTED, ProposalStatus.REJECTED),
        (ProposalStatus.FAILED, ProposalStatus.FAILED),
        (ProposalStatus.EXPIRED, ProposalStatus.EXPIRED),
        (ProposalStatus.EXPIRED, ProposalStatus.REJECTED),
        (ProposalStatus.SUPERSEDED, ProposalStatus.SUPERSEDED),
    ],
)
def test_repeat_resolve_is_idempotent(current, target):
    assert is_idempotent(current, target)
    assert resolve_transition(current, target) is True


@pytest.mark.parametrize(
    "current,target",
    [
        (ProposalStatus.APPLIED, ProposalStatus.REJECTED),
        (ProposalStatus.APPLIED, ProposalStatus.APPROVED),
        (ProposalStatus.REJECTED, ProposalStatus.APPLIED),
        (ProposalStatus.FAILED, ProposalStatus.APPLIED),
        (ProposalStatus.EXPIRED, ProposalStatus.APPLIED),
        (ProposalStatus.SUPERSEDED, ProposalStatus.APPLIED),
        (ProposalStatus.SUPERSEDED, ProposalStatus.REJECTED),
    ],
)
def test_conflicting_terminal_decision_is_a_conflict(current, target):
    with pytest.raises(ProposalConflict):
        resolve_transition(current, target)


def test_pending_move_is_not_idempotent_and_must_be_legal():
    assert resolve_transition(ProposalStatus.PROPOSED, ProposalStatus.APPLIED) is False
    with pytest.raises(IllegalProposalTransition):
        resolve_transition(ProposalStatus.PROPOSED, ProposalStatus.PROPOSED)


# ---------------------------------------------------------------------------
# transition() side effects
# ---------------------------------------------------------------------------


def test_transition_appends_audit_history_and_timestamps():
    row = _row()
    actor = uuid4()
    transition(
        row,
        ProposalStatus.APPLIED,
        action="confirm",
        actor_id=actor,
        note="looks right",
        final_payload={"title": "Pregnancy"},
        result={"entity_id": "evt-1"},
    )
    assert row.status == ProposalStatus.APPLIED
    assert row.applied_at is not None
    assert row.resolved_at is not None
    assert row.final_payload == {"title": "Pregnancy"}
    trace = row.resolution_trace
    assert trace["resolved_by"] == str(actor)
    assert trace["note"] == "looks right"
    assert trace["result"] == {"entity_id": "evt-1"}
    assert trace["error"] is None
    assert trace["history"] == [
        {
            "from": "proposed",
            "to": "applied",
            "at": trace["history"][0]["at"],
            "by": str(actor),
            "action": "confirm",
        }
    ]


def test_reject_does_not_set_applied_at():
    row = _row()
    transition(row, ProposalStatus.REJECTED, action="dismiss", note="duplicate")
    assert row.applied_at is None
    assert row.resolved_at is not None
    assert row.resolution_trace["note"] == "duplicate"


def test_approve_then_apply_keeps_both_history_entries():
    row = _row()
    transition(row, ProposalStatus.APPROVED, action="approve", actor_id=uuid4())
    transition(
        row,
        ProposalStatus.APPLIED,
        action="apply",
        actor_id=uuid4(),
        result={"entity_id": "x"},
    )
    assert [entry["to"] for entry in row.resolution_trace["history"]] == [
        "approved",
        "applied",
    ]


def test_illegal_transition_via_helper_is_typed_and_leaves_row_untouched():
    row = _row(ProposalStatus.APPLIED)
    with pytest.raises(IllegalProposalTransition):
        transition(row, ProposalStatus.APPROVED, action="approve")
    assert row.status == ProposalStatus.APPLIED
    assert row.resolution_trace == {}


# ---------------------------------------------------------------------------
# wire mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (ProposalStatus.PROPOSED, HitlTaskStatus.PROPOSED),
        (ProposalStatus.APPROVED, HitlTaskStatus.CONFIRMED),
        (ProposalStatus.APPLIED, HitlTaskStatus.CONFIRMED),
        (ProposalStatus.REJECTED, HitlTaskStatus.DISMISSED),
        (ProposalStatus.FAILED, HitlTaskStatus.FAILED),
        (ProposalStatus.EXPIRED, HitlTaskStatus.DISMISSED),
        (ProposalStatus.SUPERSEDED, HitlTaskStatus.DISMISSED),
    ],
)
def test_card_status_mapping_is_total(status, expected):
    assert card_status(status) == expected


@pytest.mark.parametrize(
    "card,expected",
    [
        (HitlTaskStatus.PROPOSED, ProposalStatus.PROPOSED),
        (HitlTaskStatus.CONFIRMED, ProposalStatus.APPLIED),
        (HitlTaskStatus.DISMISSED, ProposalStatus.REJECTED),
        (HitlTaskStatus.FAILED, ProposalStatus.FAILED),
        ("confirmed", ProposalStatus.APPLIED),
    ],
)
def test_wire_status_mapping(card, expected):
    assert proposal_status(card) == expected
