"""Schema tests for the state biomarker discriminator (plan
``dev/plans/state-biomarkers-2026-08-05.md`` Steps 1 + 2).

Covers three layers:
1. ORM metadata exposes the new enum, models, columns, and CHECK constraints.
2. The migration created the columns / tables / constraints / enum type in
   the live DB.
3. Behavioural enforcement: the two state-related CHECK constraints reject
   bad rows (STATE + telemetry, STATE + unit), and a QUANTITY biomarker is
   still accepted with the previous shape.
"""
import uuid

import pytest
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.base import Base
from app.models.biomarker_model import (
    BiomarkerDefinition,
    BiomarkerState,
)
from app.models.enums import BiomarkerValueType


# ----------------------------------------------------------------------------
# 1. ORM metadata
# ----------------------------------------------------------------------------


def test_biomarker_value_type_enum_values():
    assert {v.value for v in BiomarkerValueType} == {"quantity", "state"}
    assert BiomarkerValueType("quantity") is BiomarkerValueType.QUANTITY
    assert BiomarkerValueType("state") is BiomarkerValueType.STATE


def test_models_define_new_tables():
    md = Base.metadata
    assert "biomarker_states" in md.tables
    assert "biomarker_allowed_states" in md.tables


def test_biomarker_definition_has_new_columns():
    cols = Base.metadata.tables["biomarker_definitions"].columns
    assert "value_type" in cols
    assert "supports_multi_state" in cols


def test_models_declare_state_check_constraints():
    expected = {
        "ck_biomarker_definitions_state_not_telemetry",
        "ck_biomarker_definitions_state_no_unit",
    }
    found = set()
    for constraint in Base.metadata.tables["biomarker_definitions"].constraints:
        if (
            isinstance(constraint, CheckConstraint)
            and constraint.name in expected
        ):
            found.add(constraint.name)
    assert found == expected, f"Missing constraints: {expected - found}"


def test_state_model_unique_constraint():
    """``biomarker_states`` is unique on ``(code, system)`` so the same code
    can coexist across different code systems (POS in v3-OI vs POS in a
    custom urn)."""
    from sqlalchemy import UniqueConstraint

    uqs = {
        c.name
        for c in Base.metadata.tables["biomarker_states"].constraints
        if isinstance(c, UniqueConstraint) and c.name
    }
    assert "uq_biomarker_states_code_system" in uqs


def test_allowed_states_join_unique_constraint():
    from sqlalchemy import UniqueConstraint

    uqs = {
        c.name
        for c in Base.metadata.tables["biomarker_allowed_states"].constraints
        if isinstance(c, UniqueConstraint) and c.name
    }
    assert "uq_biomarker_allowed_states" in uqs


# ----------------------------------------------------------------------------
# 2. Live DB
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_has_new_columns_and_enum():
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT column_name, udt_name
                    FROM information_schema.columns
                    WHERE table_name = 'biomarker_definitions'
                      AND column_name IN ('value_type', 'supports_multi_state')
                """)
            )
        ).all()
        col_map = {r[0]: r[1] for r in rows}
        assert col_map.get("value_type") == "biomarkervaluetype"
        assert col_map.get("supports_multi_state") == "bool"

        enum_vals = (
            await session.execute(
                text("""
                    SELECT enumlabel FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'biomarkervaluetype'
                    ORDER BY e.enumsortorder
                """)
            )
        ).all()
        assert [r[0] for r in enum_vals] == ["quantity", "state"]


@pytest.mark.asyncio
async def test_db_has_new_tables():
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_name IN ('biomarker_states', 'biomarker_allowed_states')
                """)
            )
        ).all()
        table_names = {r[0] for r in rows}
        assert table_names == {"biomarker_states", "biomarker_allowed_states"}


@pytest.mark.asyncio
async def test_db_has_state_check_constraints():
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = 'biomarker_definitions'::regclass
                      AND contype = 'c'
                      AND conname LIKE '%state%'
                """)
            )
        ).all()
        names = {r[0] for r in rows}
        assert "ck_biomarker_definitions_state_not_telemetry" in names
        assert "ck_biomarker_definitions_state_no_unit" in names


# ----------------------------------------------------------------------------
# 3. Behavioural enforcement
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_biomarker_with_telemetry_is_rejected():
    """STATE biomarkers cannot be telemetry — ``telemetry_data.value`` is
    Float NOT NULL and categorical values have nowhere to go."""
    bad = BiomarkerDefinition(
        slug=f"__test_state_telemetry_{uuid.uuid4().hex[:8]}",
        coding_system=__import__("app.models.enums", fromlist=["CodingSystem"]).CodingSystem.LOINC,
        name="Bad state telemetry",
        aliases=[],
        is_telemetry=True,
        value_type=BiomarkerValueType.STATE,
        scope=__import__("app.models.enums", fromlist=["CatalogScope"]).CatalogScope.SYSTEM,
    )
    async with AsyncSessionLocal() as session:
        session.add(bad)
        with pytest.raises(IntegrityError) as exc:
            await session.commit()
        assert "state_not_telemetry" in str(exc.value).lower() or "check" in str(exc.value).lower()
        await session.rollback()


@pytest.mark.asyncio
async def test_state_biomarker_with_unit_is_rejected():
    """STATE biomarkers carry no unit (categorical values are unitless)."""
    from app.models.enums import CodingSystem, CatalogScope

    # Insert a real unit so the FK passes, then point a STATE biomarker at it.
    async with AsyncSessionLocal() as session:
        from app.models.biomarker_model import Unit
        from app.models.enums import QuantityType

        u = Unit(
            symbol=f"__test_unit_{uuid.uuid4().hex[:8]}",
            name="Test unit",
            quantity_type=QuantityType.OTHER,
            conversion_multiplier=1.0,
        )
        session.add(u)
        await session.flush()
        bad = BiomarkerDefinition(
            slug=f"__test_state_unit_{uuid.uuid4().hex[:8]}",
            coding_system=CodingSystem.LOINC,
            name="Bad state unit",
            aliases=[],
            is_telemetry=False,
            value_type=BiomarkerValueType.STATE,
            preferred_unit_id=u.id,
            scope=CatalogScope.SYSTEM,
        )
        session.add(bad)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_quantity_biomarker_still_works():
    """The legacy numeric shape is unchanged — QUANTITY + unit + telemetry
    toggle is accepted exactly as before."""
    from app.models.enums import CatalogScope, CodingSystem

    bio = BiomarkerDefinition(
        slug=f"__test_qty_{uuid.uuid4().hex[:8]}",
        coding_system=CodingSystem.LOINC,
        name="Plain quantity biomarker",
        aliases=[],
        is_telemetry=False,
        value_type=BiomarkerValueType.QUANTITY,
        scope=CatalogScope.SYSTEM,
    )
    async with AsyncSessionLocal() as session:
        session.add(bio)
        await session.commit()
        # cleanup
        await session.delete(bio)
        await session.commit()


@pytest.mark.asyncio
async def test_biomarker_state_unique_code_system():
    """Inserting the same (code, system) twice must fail; the same code under
    a different system must succeed.

    Uses a unique custom-system URL (not the canonical seed systems) so this
    test is independent of whether the seed catalog is loaded.
    """
    custom_system = f"urn:uuid:test-unique-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        s1 = BiomarkerState(
            slug=f"__test_dup_a_{uuid.uuid4().hex[:6]}",
            code="DUPCODE",
            system=custom_system,
            display="Dup A",
        )
        s2 = BiomarkerState(
            slug=f"__test_dup_b_{uuid.uuid4().hex[:6]}",
            code="DUPCODE",
            system=custom_system + "-alt",  # different system → distinct concept
            display="Dup B",
        )
        session.add(s1)
        session.add(s2)
        await session.flush()  # both should be accepted (different systems)

        s3 = BiomarkerState(
            slug=f"__test_dup_c_{uuid.uuid4().hex[:6]}",
            code="DUPCODE",
            system=custom_system,  # same (code, system) as s1 → must fail
            display="Dup C",
        )
        session.add(s3)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
