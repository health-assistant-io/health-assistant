"""The audit hash chain — per-chain append with a locked head row.

ADR 0054 D3 (program Phase 4A): every ``audit_events`` row extends a
per-tenant SHA-256 hash chain; the NULL-tenant system rows form their own
chain under the ``"system"`` key. The chain key lives on the row (not just
``tenant_id``) so the unique ``(chain_key, seq)`` constraint has no
NULL-distinct holes: Postgres unique indexes treat NULLs as distinct, and a
system chain keyed on ``tenant_id IS NULL`` could silently carry duplicate
sequence numbers.

Appends serialize per chain by taking a row-level lock on the chain's head
row in ``audit_chain_heads`` (``SELECT ... FOR UPDATE``) and advancing
``seq``/``head_hash`` in the same transaction as the row insert. Concurrent
appends of one chain queue on the head row; the ``audit_events`` table is
never locked. The head table (not the audit table) is the write-coordination
point, per the ADR's "bucketed chain nodes" decision — verification windows
stay bounded by walking one chain at a time.

Canonical hash — SHA-256 over the compact JSON array::

    [chain_key, seq, prev_hash, action, resource_type, resource_id, outcome,
     created_at(UTC ISO-8601)]

``user_id`` and the JSONB diff payload sit **deliberately outside** the
hash: both are rewritten by sanctioned lifecycle flows (data-subject erasure
nulls the actor and diffs; the 3650-day audit retention anonymizes the
diffs), and a chain that false-alarms on every sanctioned erasure would be
noise, not tamper-evidence. The chain proves append-only sequence
integrity — no gap, no silent deletion or reordering, and no edit of the
hashed fields. PHI never reaches the diff columns in the first place (the
write-time redaction map), so the unhashed mutable payload carries no
identity data worth protecting with the chain.

Genesis: the first row of a chain carries ``seq=1`` and
``prev_hash=GENESIS_HASH`` (64 zeros).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_model import AuditChainHead, AuditEvent

logger = logging.getLogger(__name__)

#: The NULL-tenant (system-level) chain's key.
SYSTEM_CHAIN_KEY = "system"

#: ``prev_hash`` of the first row of every chain.
GENESIS_HASH = "0" * 64

#: Log prefix of the gap/tamper alarm (D3: a gap IS a tamper alarm — there
#: is no resync marker; an operator investigates, never re-anchors).
CHAIN_TAMPER_ALARM = "AUDIT_CHAIN_ALARM"

#: Sentinel serialized for a NULL ``resource_id`` in the canonical array.
_NULL_RESOURCE = ""

#: The verification reasons, closed set (pinned by tests).
REASON_OK = "ok"
REASON_EMPTY = "empty"
REASON_GAP = "gap"
REASON_PREV_HASH = "prev_hash_mismatch"
REASON_ENTRY_HASH = "entry_hash_mismatch"
REASON_MISSING_HEAD = "missing_head"
REASON_HEAD_SEQ = "head_seq_mismatch"
REASON_HEAD_HASH = "head_hash_mismatch"


def chain_key_for(tenant_id: UUID | str | None) -> str:
    """The chain key for a tenant (``"system"`` when ``tenant_id`` is NULL).

    String tenant ids are coerced to ``UUID`` first so a non-normalized
    form (upper-case hex, missing hyphens) lands on the same chain as the
    canonical UUID spelling — a caller passing ``str`` tenant ids must not
    silently fork a parallel chain. Values that do not parse as UUIDs stay
    verbatim (tests, synthetic keys).
    """
    if tenant_id is None:
        return SYSTEM_CHAIN_KEY
    if isinstance(tenant_id, str):
        try:
            tenant_id = UUID(tenant_id)
        except ValueError:
            return f"t:{tenant_id}"
    return f"t:{tenant_id}"


def canonical_timestamp(value: datetime) -> str:
    """UTC ISO-8601 form of a row timestamp — stable across drivers."""
    if value.tzinfo is None:
        return value.isoformat() + "+00:00"
    return value.astimezone(UTC).isoformat()


def compute_entry_hash(
    *,
    chain_key: str,
    seq: int,
    prev_hash: str,
    action: str,
    resource_type: str,
    resource_id: UUID | str | None,
    outcome: str,
    created_at: datetime,
) -> str:
    """The canonical SHA-256 entry hash (see module docs for the field set)."""
    canonical = json.dumps(
        [
            chain_key,
            seq,
            prev_hash,
            action,
            resource_type,
            str(resource_id) if resource_id is not None else _NULL_RESOURCE,
            outcome,
            canonical_timestamp(created_at),
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry_hash_of(row: AuditEvent) -> str:
    """Recompute the canonical hash of a mapped row."""
    return compute_entry_hash(
        chain_key=row.chain_key,
        seq=row.seq,
        prev_hash=row.prev_hash,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        outcome=row.outcome,
        created_at=row.created_at,
    )


async def append(session: AsyncSession, entry: AuditEvent) -> None:
    """Extend ``entry``'s chain inside the caller's transaction.

    Locks the head row (creating it at ``seq=0``/genesis on first append),
    assigns ``seq``/``prev_hash``/``entry_hash`` on the entry and advances
    the head. The caller owns commit/rollback — the head lock lives exactly
    as long as the append transaction.
    """
    key = entry.chain_key
    head = (
        await session.execute(
            select(AuditChainHead).where(AuditChainHead.chain_key == key).with_for_update()
        )
    ).scalar_one_or_none()
    if head is None:
        await session.execute(
            pg_insert(AuditChainHead)
            .values(
                chain_key=key,
                tenant_id=entry.tenant_id,
                seq=0,
                head_hash=GENESIS_HASH,
            )
            .on_conflict_do_nothing()
        )
        head = (
            await session.execute(
                select(AuditChainHead).where(AuditChainHead.chain_key == key).with_for_update()
            )
        ).scalar_one()
    entry.seq = head.seq + 1
    entry.prev_hash = head.head_hash
    entry.entry_hash = compute_entry_hash(
        chain_key=entry.chain_key,
        seq=entry.seq,
        prev_hash=entry.prev_hash,
        action=entry.action,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        outcome=entry.outcome,
        created_at=entry.created_at,
    )
    head.seq = entry.seq
    head.head_hash = entry.entry_hash
    session.add(entry)


async def verify_audit_chain(db: AsyncSession, chain_key: str) -> dict[str, Any]:
    """Walk one chain (seq contiguity, hash links, recomputation, head).

    Returns ``{ok, chain_key, entries, head_seq, first_bad_seq, reason}``;
    a failure logs the ``AUDIT_CHAIN_ALARM`` critical and names the first
    bad sequence number. Never raises for a broken chain — the status dict
    is the answer.

    The rows are read **before** the head, and a head-only mismatch
    (``missing_head`` / ``head_seq_mismatch`` / ``head_hash_mismatch``)
    triggers exactly one re-read of head **and** rows before the alarm:
    the two statements otherwise span two implicit snapshots, and an
    append committing between them would present a head ahead of the rows
    — a live writer, not tampering. The head re-read bypasses the
    session's identity map (``populate_existing``): ``Session.get``
    would otherwise answer from the first pass's cached object without
    emitting SQL, and a lagging head could never catch up on the retry.
    Only a mismatch that persists across
    the re-read alarms (a genuinely lagging or deleted head does); a
    race the re-read resolves stays silent, and a broken chain alarms
    exactly once.
    """
    result = await _verify_walk(db, chain_key)
    if not result["ok"] and result["reason"] in _HEAD_ONLY_REASONS:
        result = await _verify_walk(db, chain_key)
    if not result["ok"]:
        _log_alarm(result)
    return result


#: The reasons that implicate the head row only — every row-level walk
#: already passed. A concurrent append explains each of them.
_HEAD_ONLY_REASONS = frozenset({REASON_MISSING_HEAD, REASON_HEAD_SEQ, REASON_HEAD_HASH})


async def _verify_walk(db: AsyncSession, chain_key: str) -> dict[str, Any]:
    """One rows-then-head pass over the chain (stamps, but does not log)."""
    rows = list(
        (
            await db.execute(
                select(AuditEvent).where(AuditEvent.chain_key == chain_key).order_by(AuditEvent.seq)
            )
        )
        .scalars()
        .all()
    )
    head = await db.get(AuditChainHead, chain_key, populate_existing=True)
    result: dict[str, Any] = {
        "ok": True,
        "chain_key": chain_key,
        "entries": len(rows),
        "head_seq": head.seq if head is not None else None,
        "first_bad_seq": None,
        "reason": REASON_EMPTY if not rows else REASON_OK,
    }
    expected_seq = 1
    prev = GENESIS_HASH
    for row in rows:
        if row.seq != expected_seq:
            return _failed(result, REASON_GAP, row.seq)
        if row.prev_hash != prev:
            return _failed(result, REASON_PREV_HASH, row.seq)
        if row.entry_hash != _entry_hash_of(row):
            return _failed(result, REASON_ENTRY_HASH, row.seq)
        prev = row.entry_hash
        expected_seq += 1
    if rows:
        if head is None:
            return _failed(result, REASON_MISSING_HEAD, rows[-1].seq)
        if head.seq != rows[-1].seq:
            return _failed(result, REASON_HEAD_SEQ, rows[-1].seq)
        if head.head_hash != rows[-1].entry_hash:
            return _failed(result, REASON_HEAD_HASH, rows[-1].seq)
    return result


async def verify_all_chains(db: AsyncSession) -> list[dict[str, Any]]:
    """Verify every chain (heads first, then chains with rows but no head)."""
    row_keys = set((await db.execute(select(AuditEvent.chain_key).distinct())).scalars().all())
    head_keys = set((await db.execute(select(AuditChainHead.chain_key))).scalars().all())
    return [await verify_audit_chain(db, key) for key in sorted(row_keys | head_keys)]


def _failed(
    result: dict[str, Any],
    reason: str,
    first_bad_seq: int,
) -> dict[str, Any]:
    """Stamp the failure into the result (logging happens on the final
    verdict, in :func:`verify_audit_chain`)."""
    result.update(ok=False, reason=reason, first_bad_seq=first_bad_seq)
    return result


def _log_alarm(result: dict[str, Any]) -> None:
    """Raise the tamper alarm log for a final failed verdict (exactly once)."""
    logger.critical(
        "%s chain=%s reason=%s first_bad_seq=%s entries=%s head_seq=%s",
        CHAIN_TAMPER_ALARM,
        result["chain_key"],
        result["reason"],
        result["first_bad_seq"],
        result["entries"],
        result["head_seq"],
    )
