"""Regression tests for audit B1 — per-tenant uniqueness.

The global ``UNIQUE`` on ``slug``/``mrn`` was replaced with a
``(col, COALESCE(tenant_id, <sentinel>))`` unique index on four tables so the
same slug/MRN can coexist across tenants (but not within one tenant, and not
twice among NULL-tenant/system rows).

Behavioural spot-checks run against ``biomarker_definitions`` (FK-free apart
from the optional tenant FK); the DB-existence check covers all four.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal

_NEW_INDEXES = {
    "ix_biomarker_definitions_slug_tenant",
    "ix_anatomy_structures_slug_tenant",
    "ix_clinical_event_types_slug_tenant",
    "ix_fhir_patients_mrn_tenant",
}


@pytest.mark.asyncio
async def test_coalesce_unique_indexes_exist():
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE indexname LIKE 'ix_%_tenant' "
                    "AND tablename IN ('biomarker_definitions','anatomy_structures',"
                    "'clinical_event_types','fhir_patients')"
                )
            )
        ).scalars().all()
    missing = _NEW_INDEXES - set(rows)
    assert not missing, f"Missing COALESCE unique indexes: {missing}"


def _biomarker_sql() -> str:
    return (
        "INSERT INTO biomarker_definitions "
        "(id, slug, name, coding_system, aliases, is_telemetry, scope, tenant_id) "
        "VALUES (gen_random_uuid(), :slug, 'b1', 'CUSTOM', '[]', false, 'system', :tenant)"
    )


@pytest.mark.asyncio
async def test_same_slug_allowed_across_tenants_but_not_within():
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    slug = f"b1-shared-{uuid.uuid4().hex[:8]}"
    created_slugs = []

    async with AsyncSessionLocal() as session:
        # Two real tenants.
        for tid in (tenant_a, tenant_b):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"
                ),
                {"id": tid, "n": f"T-{tid.hex[:6]}", "s": f"t-{tid.hex[:8]}"},
            )
        await session.commit()

        # Same slug in tenant A then tenant B -> both allowed.
        await session.execute(
            text(_biomarker_sql()), {"slug": slug, "tenant": tenant_a}
        )
        await session.execute(
            text(_biomarker_sql()), {"slug": slug, "tenant": tenant_b}
        )
        await session.commit()
        created_slugs.append(slug)

        # Same slug again in tenant A -> must be rejected.
        with pytest.raises(IntegrityError) as exc_info:
            async with session.begin_nested():
                await session.execute(
                    text(_biomarker_sql()), {"slug": slug, "tenant": tenant_a}
                )
        assert "ix_biomarker_definitions_slug_tenant" in str(exc_info.value)

        # Cleanup.
        await session.execute(
            text("DELETE FROM biomarker_definitions WHERE slug = :slug"),
            {"slug": slug},
        )
        await session.execute(
            text("DELETE FROM tenants WHERE id IN (:a, :b)"),
            {"a": tenant_a, "b": tenant_b},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_two_null_tenant_rows_with_same_slug_collide():
    slug = f"b1-null-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                _biomarker_sql().replace(":tenant", "NULL")
            ),
            {"slug": slug},
        )
        await session.commit()

        # A second NULL-tenant row with the same slug must collide (both map to
        # the sentinel, so the unique index treats them as same-tenant).
        with pytest.raises(IntegrityError) as exc_info:
            async with session.begin_nested():
                await session.execute(
                    text(_biomarker_sql().replace(":tenant", "NULL")),
                    {"slug": slug},
                )
        assert "ix_biomarker_definitions_slug_tenant" in str(exc_info.value)

        await session.execute(
            text("DELETE FROM biomarker_definitions WHERE slug = :slug"),
            {"slug": slug},
        )
        await session.commit()
