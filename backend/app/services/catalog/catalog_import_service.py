# ruff: noqa: B904,E501 -- long immutable strings; reflow when touched
import logging
from dataclasses import dataclass, field
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.biomarker_model import (
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerReferenceRange,
    BiomarkerState,
    Unit,
)
from app.models.enums import CatalogScope, ConceptKind, QuantityType
from app.schemas.biomarker import CatalogImportPayload
from app.services.catalog.concept_service import resolve_concept_by_slug
from app.services.seed.identity import (
    SeedIdentityConflict,
    SeedIdentityHint,
    apply_values,
    find_identity_row,
    identity_id,
)

logger = logging.getLogger(__name__)


def _biomarker_signature(bio: BiomarkerDefinition) -> tuple:
    """The mutable fields ``import_catalog`` writes, for real-change counting."""
    return (
        bio.name,
        bio.coding_system,
        bio.code,
        bio.class_concept_id,
        tuple(bio.aliases or []),
        bio.info,
        bio.reference_range_min,
        bio.reference_range_max,
        bio.preferred_unit_id,
        bio.value_type,
        bool(bio.supports_multi_state),
    )


@dataclass(frozen=True)
class CatalogItemOutcome:
    """The typed per-item result of an import (never a swallowed error).

    ``action`` is ``added | updated | unchanged | failed``; a ``failed``
    outcome carries the error text and the item's reference so the caller
    can abort the surrounding transaction (nothing is committed inside the
    importer — the caller owns the transaction, ADR 0056).
    """

    kind: str
    reference: str
    action: str
    error: str | None = None


@dataclass(frozen=True)
class CatalogImportResult:
    """Import statistics plus the per-item outcomes (typed, ordered)."""

    stats: dict[str, int] = field(default_factory=dict)
    items: tuple[CatalogItemOutcome, ...] = ()

    @property
    def failures(self) -> tuple[CatalogItemOutcome, ...]:
        return tuple(outcome for outcome in self.items if outcome.action == "failed")


@dataclass(frozen=True)
class CatalogSeedIdentities:
    """Per-item seed identities for :meth:`import_catalog` (the seed path).

    Keyed by the payload's natural reference (unit ``symbol`` / biomarker
    ``slug``); each hint carries the validated qualified ``seed_key`` and an
    optional explicit item ``id``.
    """

    units: dict[str, SeedIdentityHint]
    biomarkers: dict[str, SeedIdentityHint]


class CatalogImportService:
    def __init__(self, db: AsyncSession, *, tenant_id: UUID | None = None):
        """One importer, two doors.

        ``tenant_id=None`` is the system door (seed lane, admin URL import):
        every row is a system row and the lookup matches system rows only.
        A non-``None`` ``tenant_id`` is a **tenant restore**: the payload's
        rows must land in that tenant's scope, the lookup prefers the tenant
        overlay over a same-``slug`` system row, and a system row is never
        the update target (the seed lane owns it).
        """
        self.db = db
        self.tenant_id = tenant_id

    async def _upsert_reference_ranges(self, biomarker_id: UUID, desired) -> None:
        """Idempotently upsert stratified reference ranges for a biomarker.

        Matches an existing row by its stratification key (sex, age_min,
        age_max, unit_id) and updates the bounds; inserts when no match.
        **Upsert-only** — rows not present in ``desired`` are left untouched,
        so user-added ranges survive a re-seed/re-import.
        """
        if not desired:
            return
        existing = (
            (
                await self.db.execute(
                    select(BiomarkerReferenceRange).where(
                        BiomarkerReferenceRange.biomarker_id == biomarker_id
                    )
                )
            )
            .scalars()
            .all()
        )

        def _key(rr):
            return (
                getattr(rr, "sex", None),
                getattr(rr, "age_min", None),
                getattr(rr, "age_max", None),
                str(getattr(rr, "unit_id", None)) if getattr(rr, "unit_id", None) else None,
            )

        by_key = {}
        for rr in existing:
            by_key[_key(rr)] = rr

        for want in desired:
            k = _key(want)
            low = getattr(want, "low", None)
            high = getattr(want, "high", None)
            text = getattr(want, "text", None)
            applies_to = getattr(want, "applies_to", None)
            sex = getattr(want, "sex", None)
            age_min = getattr(want, "age_min", None)
            age_max = getattr(want, "age_max", None)
            unit_id = getattr(want, "unit_id", None)
            match = by_key.get(k)
            if match is not None:
                match.low = low
                match.high = high
                match.text = text
                match.applies_to = applies_to
            else:
                self.db.add(
                    BiomarkerReferenceRange(
                        biomarker_id=biomarker_id,
                        sex=sex,
                        age_min=age_min,
                        age_max=age_max,
                        unit_id=unit_id,
                        low=low,
                        high=high,
                        text=text,
                        applies_to=applies_to,
                    )
                )

    async def _upsert_allowed_states(self, biomarker_id: UUID, desired) -> None:
        """Idempotently upsert allowed_states for a STATE biomarker.

        ``desired`` is a list of ``AllowedStateSpec`` (state_slug, is_normal,
        sort_order). Slugs are resolved against the universal
        ``biomarker_states`` catalog. Replaces the existing set atomically
        (delete-then-insert) so the JSON is the single source of truth.
        """
        if not desired:
            return
        # Resolve slugs → state_ids.
        slugs = [getattr(s, "state_slug", None) or s.get("state_slug") for s in desired]
        slugs = [s for s in slugs if s]
        if not slugs:
            return
        state_rows = (
            (await self.db.execute(select(BiomarkerState).where(BiomarkerState.slug.in_(slugs))))
            .scalars()
            .all()
        )
        by_slug = {r.slug: r for r in state_rows}
        # Wipe existing and re-insert (atomic replace).
        from sqlalchemy import delete as sa_delete

        await self.db.execute(
            sa_delete(BiomarkerAllowedState).where(
                BiomarkerAllowedState.biomarker_id == biomarker_id
            )
        )
        for spec in desired:
            slug = getattr(spec, "state_slug", None) or spec.get("state_slug")
            state = by_slug.get(slug)
            if not state:
                logger.warning("Catalog import: unknown biomarker_state slug %r — skipping", slug)
                continue
            is_normal = getattr(spec, "is_normal", None)
            if is_normal is None:
                is_normal = spec.get("is_normal", False)
            sort_order = getattr(spec, "sort_order", None)
            if sort_order is None:
                sort_order = spec.get("sort_order", 0)
            self.db.add(
                BiomarkerAllowedState(
                    biomarker_id=biomarker_id,
                    state_id=state.id,
                    is_normal=bool(is_normal),
                    sort_order=int(sort_order),
                )
            )

    async def _find_biomarker_by_slug(self, slug: str) -> BiomarkerDefinition | None:
        """The admin-door lookup: visible rows, tenant overlay first.

        The visible set is ``(tenant == job tenant OR system)``; ordering by
        ``tenant_id IS NULL`` makes the tenant row win and ``LIMIT 1`` keeps
        the lookup total. A tenant restore whose only match is a *system* row
        treats it as absent — the system row is never mutated from tenant data
        (the seed lane owns it), so the caller creates the tenant overlay.
        """
        visible = (
            BiomarkerDefinition.tenant_id.is_(None)
            if self.tenant_id is None
            else or_(
                BiomarkerDefinition.tenant_id == self.tenant_id,
                BiomarkerDefinition.tenant_id.is_(None),
            )
        )
        row = (
            (
                await self.db.execute(
                    select(BiomarkerDefinition)
                    .where(BiomarkerDefinition.slug == slug, visible)
                    .order_by(BiomarkerDefinition.tenant_id.is_(None))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if row is not None and self.tenant_id is not None and row.tenant_id is None:
            return None
        return row

    async def _resolve_class_concept(self, bio_data) -> UUID | None:
        """Resolve a biomarker's class concept from the class-concept slug
        (``class_concept_slug`` — the canonical key the backup export and the
        ontology-catalog URL door both emit)."""
        slug = getattr(bio_data, "class_concept_slug", None)
        if not slug:
            return None
        return await resolve_concept_by_slug(self.db, slug, ConceptKind.BIOMARKER_CLASS)

    async def fetch_catalog_from_url(self, url: str) -> CatalogImportPayload:
        """Fetch JSON catalog from a URL and parse it."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                return CatalogImportPayload.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to fetch or parse catalog from {url}: {e}")
            raise ValueError(f"Failed to load catalog from URL: {e!s}")

    async def import_catalog(
        self,
        payload: CatalogImportPayload,
        *,
        identities: CatalogSeedIdentities | None = None,
    ) -> CatalogImportResult:
        """
        Import units and biomarkers from the payload into the database.

        ``identities`` (the seed path) switches the upsert to the curated
        identity model — by deterministic id, then ``seed_key``, system rows
        only (ADR 0055). Without it (the admin import door) rows upsert by
        ``symbol``/``slug`` as before. Units are the shared (global) unit
        vocabulary: a **tenant** restore (``tenant_id`` set) only resolves
        symbols read-only and reports unknown ones as failed outcomes, so one
        tenant's restore can never create or rewrite a row every tenant sees;
        the seed lane and the system import door own that table. A unit
        outside the curated identity
        (no seed-key hint) still honors an explicit item ``id`` at creation —
        a fresh install built from an export reproduces the exported unit
        ids, keeping re-exports stable; an id owned by another unit raises
        :class:`SeedIdentityConflict`.

        The importer **never commits**: it flushes at the end and returns a
        typed :class:`CatalogImportResult` whose per-item outcomes name every
        failure (nothing is swallowed) — the caller owns the transaction and
        aborts it when any outcome failed (ADR 0056 Phase 2).
        ``*_unchanged`` counters report rows whose values already matched
        (a fully-matching import writes nothing); ``errors`` counts
        per-item failures.
        """
        stats = {
            "units_added": 0,
            "units_updated": 0,
            "units_unchanged": 0,
            "biomarkers_added": 0,
            "biomarkers_updated": 0,
            "biomarkers_unchanged": 0,
            "errors": 0,
        }
        outcomes: list[CatalogItemOutcome] = []

        # 1. Process Units
        unit_map = {}
        for unit_data in payload.units:
            try:
                # Find existing unit by symbol
                result = await self.db.execute(select(Unit).where(Unit.symbol == unit_data.symbol))
                existing_unit = result.scalar_one_or_none()

                q_type = QuantityType.OTHER
                try:
                    if unit_data.quantity_type:
                        q_type = QuantityType(unit_data.quantity_type.upper())
                except ValueError:
                    logger.warning(
                        f"Invalid quantity type '{unit_data.quantity_type}' for unit '{unit_data.symbol}'. Using OTHER."
                    )

                unit_values = {
                    "symbol": unit_data.symbol,
                    "name": unit_data.name,
                    "quantity_type": q_type,
                }
                # ``units`` is the deployment's shared vocabulary: the table
                # is global (no tenant column), so a tenant restore must never
                # create or mutate a row another tenant can see. It resolves
                # symbols read-only and reports a symbol the vocabulary does
                # not carry; the seed lane / system door owns the writes.
                if self.tenant_id is not None:
                    if existing_unit is None:
                        stats["errors"] += 1
                        outcomes.append(
                            CatalogItemOutcome(
                                "unit",
                                unit_data.symbol,
                                "failed",
                                "unit symbol is not in the shared vocabulary; a "
                                "tenant restore cannot write the global unit table",
                            )
                        )
                        continue
                    stats["units_unchanged"] += 1
                    outcomes.append(CatalogItemOutcome("unit", unit_data.symbol, "unchanged"))
                    unit_map[existing_unit.symbol.lower()] = existing_unit.id
                    continue
                hint = identities.units.get(unit_data.symbol) if identities else None
                if hint:
                    existing_unit = await find_identity_row(
                        self.db, Unit, hint.qualified, explicit_id=hint.explicit_id
                    )
                if existing_unit:
                    if hint is None:
                        changed = apply_values(
                            existing_unit, {"name": unit_data.name, "quantity_type": q_type}
                        )
                    else:
                        changed = apply_values(existing_unit, unit_values)
                    stats["units_updated" if changed else "units_unchanged"] += 1
                    outcomes.append(
                        CatalogItemOutcome(
                            "unit", unit_data.symbol, "updated" if changed else "unchanged"
                        )
                    )
                    unit_map[existing_unit.symbol.lower()] = existing_unit.id
                else:
                    new_unit = Unit(
                        symbol=unit_data.symbol,
                        name=unit_data.name,
                        quantity_type=q_type,
                    )
                    if hint:
                        new_unit.id = identity_id(hint.qualified, hint.explicit_id)
                    elif unit_data.id is not None:
                        # No seed-key hint (an export carries none for units):
                        # an explicit item ``id`` is honored at creation so a
                        # fresh install reproduces the exported identity and a
                        # re-export is stable. An id already taken by another
                        # unit is a typed conflict, never a silent PK 500.
                        taken = await self.db.scalar(
                            select(Unit.id).where(
                                Unit.id == unit_data.id, Unit.symbol != unit_data.symbol
                            )
                        )
                        if taken is not None:
                            raise SeedIdentityConflict(
                                f"unit id {unit_data.id} already belongs to another unit — "
                                f"symbol {unit_data.symbol!r} cannot claim it"
                            )
                        new_unit.id = unit_data.id
                    self.db.add(new_unit)
                    await self.db.flush()
                    stats["units_added"] += 1
                    outcomes.append(CatalogItemOutcome("unit", unit_data.symbol, "added"))
                    unit_map[new_unit.symbol.lower()] = new_unit.id
            except Exception as e:
                logger.error(f"Error processing unit {unit_data.symbol}: {e}")
                stats["errors"] += 1
                outcomes.append(CatalogItemOutcome("unit", unit_data.symbol, "failed", str(e)))

        # Re-fetch all units to ensure our map is complete for biomarker mapping
        all_units_result = await self.db.execute(select(Unit))
        for u in all_units_result.scalars().all():
            unit_map[u.symbol.lower()] = u.id

        # 2. Process Biomarkers
        for bio_data in payload.biomarkers:
            try:
                # Resolve preferred unit
                pref_unit_id = None
                if bio_data.preferred_unit_symbol:
                    pref_unit_id = unit_map.get(bio_data.preferred_unit_symbol.lower())

                hint = identities.biomarkers.get(bio_data.slug) if identities else None
                if hint:
                    # Identity path (seed): by deterministic id, then seed_key,
                    # system rows only — never by slug.
                    existing_bio = await find_identity_row(
                        self.db, BiomarkerDefinition, hint.qualified, explicit_id=hint.explicit_id
                    )
                else:
                    # Admin door: find existing by slug, tenant overlay first.
                    # A tenant restore never adopts a system row as its update
                    # target; it creates/maintains its own overlay instead.
                    existing_bio = await self._find_biomarker_by_slug(bio_data.slug)

                if existing_bio:
                    before = _biomarker_signature(existing_bio)
                    existing_bio.name = bio_data.name
                    existing_bio.coding_system = bio_data.coding_system
                    existing_bio.code = bio_data.code
                    if bio_data.class_concept_id is not None:
                        existing_bio.class_concept_id = bio_data.class_concept_id
                    else:
                        existing_bio.class_concept_id = await self._resolve_class_concept(bio_data)
                    existing_bio.aliases = bio_data.aliases
                    existing_bio.info = bio_data.info
                    existing_bio.reference_range_min = bio_data.reference_range_min
                    existing_bio.reference_range_max = bio_data.reference_range_max
                    if pref_unit_id:
                        existing_bio.preferred_unit_id = pref_unit_id
                    # State biomarker discriminator (plan Step 4/6).
                    if bio_data.value_type:
                        existing_bio.value_type = bio_data.value_type
                        existing_bio.supports_multi_state = bio_data.supports_multi_state or False
                    await self._upsert_reference_ranges(
                        existing_bio.id, getattr(bio_data, "reference_ranges", None)
                    )
                    await self._upsert_allowed_states(
                        existing_bio.id, getattr(bio_data, "allowed_states", None)
                    )
                    if existing_bio.seed_key is None and hint is not None:
                        existing_bio.seed_key = hint.qualified
                    changed = _biomarker_signature(existing_bio) != before
                    stats["biomarkers_updated" if changed else "biomarkers_unchanged"] += 1
                    outcomes.append(
                        CatalogItemOutcome(
                            "biomarker", bio_data.slug, "updated" if changed else "unchanged"
                        )
                    )
                else:
                    class_concept_id = bio_data.class_concept_id
                    if class_concept_id is None:
                        class_concept_id = await self._resolve_class_concept(bio_data)
                    new_bio = BiomarkerDefinition(
                        slug=bio_data.slug,
                        name=bio_data.name,
                        coding_system=bio_data.coding_system,
                        code=bio_data.code,
                        class_concept_id=class_concept_id,
                        aliases=bio_data.aliases,
                        info=bio_data.info,
                        reference_range_min=bio_data.reference_range_min,
                        reference_range_max=bio_data.reference_range_max,
                        preferred_unit_id=pref_unit_id,
                        value_type=bio_data.value_type,
                        supports_multi_state=bio_data.supports_multi_state or False,
                        tenant_id=self.tenant_id,
                        scope=(
                            CatalogScope.TENANT
                            if self.tenant_id is not None
                            else CatalogScope.SYSTEM
                        ),
                    )
                    if hint:
                        new_bio.id = identity_id(hint.qualified, hint.explicit_id)
                        new_bio.seed_key = hint.qualified
                    self.db.add(new_bio)
                    await self.db.flush()  # populate new_bio.id for the FK
                    await self._upsert_reference_ranges(
                        new_bio.id, getattr(bio_data, "reference_ranges", None)
                    )
                    await self._upsert_allowed_states(
                        new_bio.id, getattr(bio_data, "allowed_states", None)
                    )
                    stats["biomarkers_added"] += 1
                    outcomes.append(CatalogItemOutcome("biomarker", bio_data.slug, "added"))
            except Exception as e:
                logger.error(f"Error processing biomarker {bio_data.slug}: {e}")
                stats["errors"] += 1
                outcomes.append(CatalogItemOutcome("biomarker", bio_data.slug, "failed", str(e)))

        await self.db.flush()
        return CatalogImportResult(stats=stats, items=tuple(outcomes))
