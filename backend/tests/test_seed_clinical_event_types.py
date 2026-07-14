"""Tests for the clinical_event_types seed loader.

Pins two contracts:
1. The shipped ``clinical_event_types.json`` loads cleanly (no validation
   errors) — guards against drift between the seed file and the typed
   ``MetadataSchema`` Pydantic model.
2. A malformed ``metadata_schema`` is rejected (counted in ``errors``, never
   written) — the fail-loud rule for the metadata descriptor.
"""
import pytest

from app.core.database import AsyncSessionLocal
from app.models.clinical_event import ClinicalEventType
from app.services.seed_service import SeedService
from sqlalchemy import select


@pytest.mark.asyncio
async def test_shipped_seed_loads_without_errors():
    """The real clinical_event_types.json validates against MetadataSchema and
    upserts cleanly. This catches any drift the moment the seed file is edited
    in a way that breaks the typed contract."""
    svc = SeedService()
    stats = await svc.seed_clinical_event_types()
    for k in ("added", "updated", "skipped", "errors"):
        assert k in stats and isinstance(stats[k], int), f"bad stats: {stats}"
    assert stats["errors"] == 0, (
        f"seed had validation errors (the shipped JSON is malformed): {stats}"
    )
    # The shipped seed declares >=9 types; all should be added or updated.
    assert stats["added"] + stats["updated"] >= 9, stats


@pytest.mark.asyncio
async def test_shipped_seed_uses_new_catalog_select_shape():
    """The pain-episode type's body_location field must use the new
    catalog-select + catalogs shape (not the legacy creatable-select/source)."""
    svc = SeedService()
    await svc.seed_clinical_event_types()
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ClinicalEventType).where(
                    ClinicalEventType.slug == "pain-episode"
                )
            )
        ).scalar_one_or_none()
    assert row is not None, "pain-episode type not seeded"
    fields = row.metadata_schema["fields"]
    body = next(f for f in fields if f["name"] == "body_location")
    assert body["type"] == "catalog-select"
    assert body["catalogs"] == ["anatomy"]
    # Legacy keys must be gone (breaking change, no backwards-compat).
    assert "source" not in body
    assert body.get("multi") is False


@pytest.mark.asyncio
async def test_malformed_metadata_schema_rejected_and_not_written():
    """A type whose metadata_schema fails MetadataSchema validation is counted
    in errors and never persisted. A well-formed sibling type in the same batch
    still loads (per-item isolation)."""
    import uuid

    # UUID-suffix the slugs so the test is isolated from accumulated DB state
    # across sessions (the conftest runs migrations but doesn't truncate
    # tables, so fixed slugs would hit the UPDATE path on a second run).
    ns = uuid.uuid4().hex[:8]
    svc = SeedService()
    payload = {
        "items": [
            {
                "slug": f"valid-no-schema-{ns}",
                "name": "Valid No Schema",
                "category_slug": "routine-wellness",
                # No metadata_schema — fine (Optional).
            },
            {
                "slug": f"valid-with-schema-{ns}",
                "name": "Valid With Schema",
                "category_slug": "routine-wellness",
                "metadata_schema": {
                    "fields": [
                        {
                            "name": "site",
                            "label": "Site",
                            "type": "catalog-select",
                            "catalogs": ["anatomy"],
                        }
                    ]
                },
            },
            {
                "slug": f"bad-catalog-select-no-catalogs-{ns}",
                "name": "Bad",
                "category_slug": "routine-wellness",
                "metadata_schema": {
                    "fields": [
                        # catalog-select without catalogs — must fail validation.
                        {"name": "x", "label": "X", "type": "catalog-select"}
                    ]
                },
            },
        ]
    }

    # Bypass file IO by calling _process directly with a live session.
    async with AsyncSessionLocal() as session:
        stats = await svc._process_clinical_event_types(session, payload)
        await session.commit()

    assert stats["errors"] == 1, f"expected 1 error, got {stats}"
    assert stats["added"] == 2, f"expected 2 added, got {stats}"

    # The bad type must NOT be in the DB.
    async with AsyncSessionLocal() as db:
        bad = (
            await db.execute(
                select(ClinicalEventType).where(
                    ClinicalEventType.slug
                    == f"bad-catalog-select-no-catalogs-{ns}"
                )
            )
        ).scalar_one_or_none()
    assert bad is None, "malformed type was written — fail-loud contract broken"

    # The valid types ARE present.
    async with AsyncSessionLocal() as db:
        good = (
            await db.execute(
                select(ClinicalEventType).where(
                    ClinicalEventType.slug == f"valid-with-schema-{ns}"
                )
            )
        ).scalar_one_or_none()
    assert good is not None
    assert good.metadata_schema["fields"][0]["catalogs"] == ["anatomy"]
