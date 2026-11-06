"""The HITL proposal-kind contract wall (plan 5 W1).

Pins what a hand edit can silently break:

* the vendored contract's ids/card variants are unique and the namespace is
  the frozen one;
* every ``schema_ref`` resolves to a registered pydantic model and every
  ``resolver`` to a static resolver (no dynamic imports under ``app/extensions``);
* the uuid5 id scheme is reproducible (representative ids pinned — changing the
  namespace or the name format re-keys every stored ``proposals.kind_id``);
* the migration's frozen rows equal the live contract;
* the seeded ``proposal_kinds`` table equals the contract (constitution rule 7b:
  seed ≡ registry) and the boot seeder is idempotent + reconciles fields;
* the four patient-record kinds ship in the contract from day one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.core.database import AsyncSessionLocal
from app.extensions.proposal_kinds import (
    CONTRACT_PATH,
    KIND_IDS,
    KINDS,
    PROPOSAL_KIND_NAMESPACE,
    get_kind,
    kind_for_card_variant,
    kind_rows,
    proposal_kind_id,
    seed_proposal_kinds,
)
from app.extensions.sdk.proposal_schemas import PAYLOAD_SCHEMAS, schema_for
from app.extensions.sdk.proposals import PATIENT_RECORD_RESOLVERS
from app.models.proposal import ProposalKind
from app.services.proposals.proposal_resolvers import CATALOG_KINDS, CLIENT_COMMITTED_RESOLVERS

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0004_proposals_unified.py"

PATIENT_RECORD_KINDS = {
    "create_event",
    "create_examination",
    "add_medication",
    "add_biomarker_to_examination",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0004", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# contract shape
# ---------------------------------------------------------------------------


def test_contract_file_is_the_vendored_copy():
    """The loader reads the vendored artefact next to the module (ADR-0031)."""
    assert CONTRACT_PATH.name == "hitl-proposal-kinds.json"
    assert CONTRACT_PATH.parent == BACKEND / "app" / "extensions"
    assert CONTRACT_PATH.is_file()


def test_kind_ids_and_card_variants_are_unique():
    assert len(set(KIND_IDS)) == len(KIND_IDS)
    assert len({kind.card_variant for kind in KINDS}) == len(KINDS)
    assert list(KIND_IDS) == [kind.id for kind in KINDS]


def test_patient_record_kinds_ship_from_day_one():
    assert set(KIND_IDS) >= PATIENT_RECORD_KINDS


def test_every_tier_is_one_or_two():
    assert {kind.tier for kind in KINDS} <= {1, 2}


def test_every_schema_ref_is_registered_and_loads():
    referenced = {kind.schema_ref for kind in KINDS}
    assert referenced == set(PAYLOAD_SCHEMAS), (
        "the contract and PAYLOAD_SCHEMAS must agree — an unregistered schema_ref "
        "would make final_payload validation impossible (trust boundary)"
    )
    for kind in KINDS:
        assert schema_for(kind.schema_ref) is not None


def test_every_resolver_is_registered():
    known = set(PATIENT_RECORD_RESOLVERS) | set(CATALOG_KINDS) | set(CLIENT_COMMITTED_RESOLVERS)
    for kind in KINDS:
        assert kind.resolver in known, f"{kind.id}: unknown resolver {kind.resolver!r}"


def test_card_variant_lookup_is_stable():
    for kind in KINDS:
        assert get_kind(kind.id) == kind
        assert kind_for_card_variant(kind.card_variant) == kind


def test_create_event_card_variant_keeps_the_adr_0015_wire_key():
    """The chat UI registers its handler under ``create_clinical_event``; the
    kind id is the write vocabulary. The card contract is unchanged."""
    kind = get_kind("create_event")
    assert kind is not None
    assert kind.card_variant == "create_clinical_event"


def test_kind_ids_are_pinned_by_the_namespace_contract():
    assert str(PROPOSAL_KIND_NAMESPACE) == "ea723d3b-42fd-527e-abb9-ae4d0f5b2946"
    assert str(proposal_kind_id("create_event")) == "92eb5573-d6bd-54e4-960c-d5f5e63c57ff"
    assert (
        str(proposal_kind_id("add_biomarker_to_examination"))
        == "fc11d6d9-55f5-5cef-a7d6-4e90a22ff263"
    )


def test_migration_kind_rows_match_the_contract():
    migration = _load_migration()
    frozen = {
        code: (kind_id, tier, schema_ref, resolver, card_variant)
        for kind_id, code, tier, schema_ref, resolver, card_variant in migration._PROPOSAL_KINDS
    }
    live = {
        row["code"]: (
            str(row["id"]),
            row["tier"],
            row["schema_ref"],
            row["resolver"],
            row["card_variant"],
        )
        for row in kind_rows()
    }
    assert frozen == live, (
        "0004_proposals_unified's frozen kind rows drifted from the contract — a "
        "new kind belongs in the contract + a data migration, never a silent edit"
    )


# ---------------------------------------------------------------------------
# the seeded lookup (rule 7b: seed ≡ registry)
# ---------------------------------------------------------------------------


async def test_seeded_proposal_kinds_match_the_contract():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(ProposalKind))).scalars().all()
    seeded = {row.code: row for row in rows}
    assert set(seeded) == set(KIND_IDS), "proposal_kinds must carry exactly the contract"
    for kind in KINDS:
        row = seeded[kind.id]
        assert row.tier == kind.tier
        assert row.schema_ref == kind.schema_ref
        assert row.resolver == kind.resolver
        assert row.card_variant == kind.card_variant
        assert row.enabled is True


async def test_boot_seeder_is_idempotent_and_reconciles_fields():
    async with AsyncSessionLocal() as session:
        await seed_proposal_kinds(session, commit=True)
        await seed_proposal_kinds(session, commit=True)
        total = (await session.execute(select(ProposalKind))).scalars().all()

        await session.execute(
            update(ProposalKind)
            .where(ProposalKind.code == "add_medication")
            .values(resolver="drifted")
        )
        await session.commit()
        await seed_proposal_kinds(session, commit=True)

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(ProposalKind).where(ProposalKind.code == "add_medication"))
        ).scalar_one()

    assert len(total) == len(KIND_IDS)
    assert row.resolver == "add_medication"


@pytest.mark.parametrize("card_variant", ["ask_user", "add_medication", "create_clinical_event"])
async def test_card_variant_is_unique_in_the_database(card_variant):
    """The DB enforces card-variant uniqueness (one wire key → one kind)."""
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ProposalKind).where(ProposalKind.card_variant == card_variant)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
