"""Proposal-kind contract loading + seeding (plan 5 W1).

The kind vocabulary is the family contract file
``contracts/hitl-proposal-kinds.json`` (canonical in the the family standards repo, vendored
byte-identically next to this module through
``the family sync tooling``). It is an *extensible domain
vocabulary* (constitution rule 7b): rows are seeded into ``proposal_kinds`` at
boot from this file, so adding a kind is a contract entry plus a row — never a
migration and never a CHECK.

Ids are deterministic (uuid5 over the kind id) so seeding is idempotent and a
proposal row's FK target is derivable without a lookup. The frozen namespace
and id format are a schema contract — changing either re-keys the vocabulary
(``tests/test_hitl_proposal_kinds.py`` pins representative ids).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proposal import ProposalKind

#: The vendored contract, byte-identical to
#: ``the family contract canonical/hitl-proposal-kinds.json``.
CONTRACT_PATH = Path(__file__).with_name("hitl-proposal-kinds.json")

#: Frozen namespace for kind ids. Mirrors the contract's ``namespace`` string;
#: the parity test asserts they agree (a changed name re-keys every stored FK).
PROPOSAL_KIND_NAMESPACE = uuid5(NAMESPACE_URL, "https://neuronection.com/hitl/proposal-kinds")


@dataclass(frozen=True)
class ProposalKindDef:
    """One entry of the HITL proposal-kind contract."""

    id: str
    tier: int
    schema_ref: str
    resolver: str
    card_variant: str


def _load_contract() -> tuple[ProposalKindDef, ...]:
    data = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if data.get("namespace") != "https://neuronection.com/hitl/proposal-kinds":
        raise RuntimeError(
            f"{CONTRACT_PATH.name}: namespace {data.get('namespace')!r} does not match "
            "the frozen PROPOSAL_KIND_NAMESPACE name"
        )
    entries: list[ProposalKindDef] = []
    for raw in data.get("kinds") or []:
        try:
            entry = ProposalKindDef(
                id=str(raw["id"]),
                tier=int(raw["tier"]),
                schema_ref=str(raw["schema_ref"]),
                resolver=str(raw["resolver"]),
                card_variant=str(raw["card_variant"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{CONTRACT_PATH.name}: malformed kind entry {raw!r}: {exc}"
            ) from exc
        if entry.tier not in (1, 2):
            raise RuntimeError(f"{CONTRACT_PATH.name}: kind {entry.id!r} has tier {entry.tier!r}")
        if ":" not in entry.schema_ref:
            raise RuntimeError(
                f"{CONTRACT_PATH.name}: kind {entry.id!r} schema_ref must be module:Class"
            )
        entries.append(entry)
    if not entries:
        raise RuntimeError(f"{CONTRACT_PATH.name}: the kind contract is empty")
    return tuple(entries)


#: The contract vocabulary in file order (the four patient-record kinds lead).
KINDS: tuple[ProposalKindDef, ...] = _load_contract()
KIND_IDS: tuple[str, ...] = tuple(kind.id for kind in KINDS)
_BY_ID = {kind.id: kind for kind in KINDS}
_BY_CARD_VARIANT = {kind.card_variant: kind for kind in KINDS}

if len(_BY_ID) != len(KINDS) or len(_BY_CARD_VARIANT) != len(KINDS):
    raise RuntimeError(f"{CONTRACT_PATH.name}: kind ids and card variants must be unique")


def get_kind(value: str) -> ProposalKindDef | None:
    """Resolve a kind id or an ADR-0015 card variant to its contract entry.

    Both vocabularies are accepted because chat cards carry the wire card key
    (``task_type`` — family law pins the card shape) while extension specs and
    the database carry the kind id.
    """
    return _BY_ID.get(value) or _BY_CARD_VARIANT.get(value)


def kind_for_card_variant(card_variant: str) -> ProposalKindDef | None:
    """Resolve the wire card key to its kind (chat persistence path)."""
    return _BY_CARD_VARIANT.get(card_variant)


def proposal_kind_id(code: str) -> UUID:
    """The deterministic id for a proposal kind code."""
    return uuid5(PROPOSAL_KIND_NAMESPACE, f"proposal_kinds/{code}")


def kind_rows() -> list[dict]:
    """The contract as ``proposal_kinds`` rows."""
    return [
        {
            "id": proposal_kind_id(kind.id),
            "code": kind.id,
            "tier": kind.tier,
            "schema_ref": kind.schema_ref,
            "resolver": kind.resolver,
            "card_variant": kind.card_variant,
            "enabled": True,
        }
        for kind in KINDS
    ]


async def seed_proposal_kinds(db: AsyncSession, *, commit: bool = False) -> dict[str, int]:
    """Upsert every contract kind into ``proposal_kinds`` (idempotent).

    Boot seeding is data, not DDL: a new kind appears here on the next start
    without a migration. Reconciles tier/schema_ref/resolver/card_variant so a
    contract edit reaches existing deployments.
    """
    rows = kind_rows()
    existing_codes = set((await db.execute(select(ProposalKind.code))).scalars().all())
    added = sum(1 for row in rows if row["code"] not in existing_codes)
    if rows:
        statement = pg_insert(ProposalKind).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[ProposalKind.code],
            set_={
                "tier": statement.excluded.tier,
                "schema_ref": statement.excluded.schema_ref,
                "resolver": statement.excluded.resolver,
                "card_variant": statement.excluded.card_variant,
                "enabled": statement.excluded.enabled,
            },
        )
        await db.execute(statement)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return {"added": added, "updated": len(rows) - added, "skipped": 0, "errors": 0}


async def ensure_proposal_kind(db: AsyncSession, code: str) -> UUID | None:
    """Guarantee the row for ``code`` exists; return its id.

    Returns ``None`` for a code the contract does not declare — callers then
    reject the proposal rather than invent a vocabulary value.
    """
    kind = get_kind(code)
    if kind is None:
        return None
    statement = (
        pg_insert(ProposalKind)
        .values(
            id=proposal_kind_id(kind.id),
            code=kind.id,
            tier=kind.tier,
            schema_ref=kind.schema_ref,
            resolver=kind.resolver,
            card_variant=kind.card_variant,
            enabled=True,
        )
        .on_conflict_do_nothing(index_elements=[ProposalKind.code])
    )
    await db.execute(statement)
    return proposal_kind_id(kind.id)


async def proposal_kinds_by_code(db: AsyncSession) -> dict[str, ProposalKind]:
    """Every seeded kind keyed by code (diagnostics/parity tests)."""
    result = await db.execute(select(ProposalKind))
    return {row.code: row for row in result.scalars().all()}


__all__ = [
    "CONTRACT_PATH",
    "KINDS",
    "KIND_IDS",
    "PROPOSAL_KIND_NAMESPACE",
    "ProposalKindDef",
    "ensure_proposal_kind",
    "get_kind",
    "kind_for_card_variant",
    "kind_rows",
    "proposal_kind_id",
    "proposal_kinds_by_code",
    "seed_proposal_kinds",
]
