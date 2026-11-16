"""The unified catalog write contract (plan A Phase 2 — ADR 0055 D5).

**One write service per catalog type.** Every catalog write — create, update,
delete, promote — flows through exactly one service function here, regardless
of the entry point:

- the generic ``POST/PUT/DELETE /catalogs/{type}`` meta-layer (validates the
  payload against the type's Pydantic contract first, then calls these
  functions via the descriptor's adapter), and
- the domain routes (``POST /medications/catalog``, ``POST /biomarkers/``,
  ``POST /concepts``, ``POST /anatomy``, ...) which are thin: validate → call
  the same function → serialize.

Each function owns ALL business logic for its operation: scope assignment via
:data:`~app.catalogs.policy.DEFAULT_CATALOG_POLICY`, tenant filtering, audit,
slug derivation, unit-symbol / class-concept resolution, ``reference_ranges``,
``allowed_states``, codes, and conflict translation (``IntegrityError`` →
:class:`~app.catalogs.policy.CatalogUniqueViolation` → HTTP 409, never a raw
500). Every write returns the **full serialized row** — the same keys the read
endpoints return, including computed fields (``class_concept_slug``,
``preferred_unit_symbol``, ``allowed_states``, codes, ``seed_key``).

Contract (ADR 0055):

- ``scope`` / ``tenant_id`` / ``created_by`` / ``seed_key`` / ``id`` /
  timestamps are never mass-assigned — they are not on the write schemas
  (``extra="forbid"`` → 422) and are stamped here from the actor.
- ``seed_key`` is response-only (seeds/imports write it directly).
- ``coding_system``/``code`` are writable on medication/allergy (and carry
  through biomarker/vaccine/concept where the columns exist).
- All resolution lookups are tenant-explicit: they match system rows
  (``tenant_id IS NULL``) **or the actor's tenant**, never a global match that
  would let one tenant shadow another. Explicit reference ids (``class_concept_id``,
  concept ``parent_id``) are validated against the same visibility rule and an
  unknown/invisible id or slug raises :class:`~app.catalogs.policy.CatalogReferenceError`
  (→ 400, no leaked name) — never a silent drop and never a cross-tenant link.
- **Reference display is visibility-projected**: the serializers resolve
  ``class_concept_slug`` / ``class_concept_name`` / ``parent_slug`` only when
  the referenced concept is visible to the caller (system row or the caller's
  tenant, ``deleted_at IS NULL``). Rows written before explicit-reference
  validation (08a22381) can carry links to hidden concepts — those project to
  ``None`` on read (the row's own ``class_concept_id`` / ``parent_id`` keeps
  its stored value; the stored FK is never rewritten).
- **Reference precedence is uniform** across every catalog type: a sent
  ``class_concept_id`` wins over ``class_concept_slug`` — on updates an
  explicit ``null`` id clears the link and any slug sent alongside is ignored
  (fields-set semantics); on creates a non-null id wins, otherwise the slug
  resolves. Either reference that is unknown/invisible raises the typed 400.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs.policy import (
    DEFAULT_CATALOG_POLICY,
    CatalogPermissionDenied,
    CatalogReferenceError,
    CatalogUniqueViolation,
)
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import (
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerReferenceRange,
    BiomarkerState,
    Unit,
)
from app.models.concept_model import Concept
from app.models.enums import CatalogScope, ConceptKind
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.schemas.allergy import AllergyCatalogCreate, AllergyCatalogUpdate
from app.schemas.anatomy import AnatomyStructureCreate, AnatomyStructureUpdate
from app.schemas.biomarker import AllowedStateSpec, BiomarkerCreate, BiomarkerUpdate
from app.schemas.concept import ConceptCreate, ConceptUpdate
from app.schemas.medication import MedicationCatalogCreate, MedicationCatalogUpdate
from app.schemas.vaccine import VaccineCatalogCreate, VaccineCatalogUpdate
from app.services.catalog.concept_service import (
    ConceptNotFoundError,
    ConceptService,
    require_visible_concept,
    resolve_concept_by_slug,
)
from app.services.fhir.fhir_helpers import assert_valid_fhir
from app.services.seed.provenance import mark_user_modified_for_model

logger = logging.getLogger(__name__)

# Unique-index name → the writable field whose duplicate triggered it. Used to
# translate a raw IntegrityError into a typed, client-facing 409.
_UNIQUE_INDEX_FIELDS: dict[str, str] = {
    "ix_medication_catalog_seed_key_tenant": "seed_key",
    "ix_medication_catalog_code_tenant": "code",
    "ix_allergy_catalog_seed_key_tenant": "seed_key",
    "ix_allergy_catalog_code_tenant": "code",
    "ix_vaccine_catalog_seed_key_tenant": "seed_key",
    "ix_vaccine_catalog_slug_tenant": "slug",
    "ix_biomarker_definitions_seed_key_tenant": "seed_key",
    "ix_concepts_seed_key_tenant": "seed_key",
    "ix_concepts_slug_tenant": "slug",
}


def _translate_integrity(exc: IntegrityError) -> CatalogUniqueViolation | CatalogReferenceError:
    """Translate a flushed ``IntegrityError`` into the typed client error.

    A unique-index violation becomes the field-named 409 conflict; a foreign-key
    violation (a reference that slipped past pre-validation) becomes the typed
    400 (:class:`~app.catalogs.policy.CatalogReferenceError`). Either way the
    caller gets a typed error after rollback — never a raw 500, and the
    message never carries another row's data.
    """
    text = str(exc)
    orig_name = type(getattr(exc, "orig", None)).__name__
    if "ForeignKeyViolation" in orig_name or "violates foreign key constraint" in text:
        return CatalogReferenceError(
            "reference", message="a referenced record does not exist or is not visible"
        )
    for index, field in _UNIQUE_INDEX_FIELDS.items():
        if index in text:
            return CatalogUniqueViolation(field, message=f"catalog {field} already in use")
    return CatalogUniqueViolation("unique", message="catalog entry violates a uniqueness rule")


async def _audit(
    db: AsyncSession,
    actor: Any,
    catalog_type: str,
    obj: Any,
    operation: str,
    *,
    from_scope: str | None = None,
    to_scope: str | None = None,
) -> None:
    """Best-effort audit row after a successful write (never aborts)."""
    from app.services.catalog.catalog_audit_service import record_from_obj

    try:
        await record_from_obj(
            db,
            actor=actor,
            catalog_type=catalog_type,
            obj=obj,
            operation=operation,
            from_scope=from_scope,
            to_scope=to_scope,
        )
    except Exception:
        logger.warning(
            "catalog audit record failed (type=%s op=%s)", catalog_type, operation, exc_info=True
        )


async def _audit_snapshot(
    db: AsyncSession, actor: Any, catalog_type: str, item_id: UUID, item_name: str, operation: str
) -> None:
    """Best-effort audit row for a deleted item (obj no longer loaded)."""
    from app.services.catalog.catalog_audit_service import record

    try:
        await record(
            db,
            actor=actor,
            catalog_type=catalog_type,
            item_id=item_id,
            item_name=item_name,
            operation=operation,
        )
    except Exception:
        logger.warning(
            "catalog audit record failed (type=%s op=%s item=%s)",
            catalog_type,
            operation,
            item_id,
            exc_info=True,
        )


async def _resolve_class_concept(
    db: AsyncSession,
    *,
    tenant_id: UUID | None,
    class_concept_id: UUID | None,
    class_concept_slug: str | None = None,
    kind: ConceptKind | None = None,
) -> UUID | None:
    """Tenant-explicit class-concept resolution: an explicit id wins (validated
    against visibility — the target must be a system row or the actor's
    tenant); a slug resolves against system rows + the actor's tenant only.

    An id that is missing/invisible and a slug that resolves to nothing raise
    :class:`~app.catalogs.policy.CatalogReferenceError` (→ 400) — never a
    silent drop of the link and never a cross-tenant one. This helper serves
    the create paths (a non-null id wins, otherwise the slug resolves; there
    is no stored link to clear); the update paths use fields-set precedence —
    a sent ``null`` id clears the link and ignores any slug alongside.
    """
    if class_concept_id is not None:
        return await require_visible_concept(db, class_concept_id, tenant_id)
    if class_concept_slug:
        resolved = await resolve_concept_by_slug(db, class_concept_slug, kind, tenant_id=tenant_id)
        if resolved is None:
            raise CatalogReferenceError("class concept")
        return resolved
    return None


async def _load_visible(
    db: AsyncSession,
    model: type,
    item_id: UUID,
    tenant_id: UUID | None,
    *,
    soft_delete: bool = False,
) -> Any | None:
    """Load one row visible to the caller (system + own tenant) or None."""
    stmt = select(model).where(
        model.id == item_id,
        or_(model.tenant_id.is_(None), model.tenant_id == tenant_id),
    )
    if soft_delete:
        stmt = stmt.where(model.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


def _label(obj: Any) -> str:
    for attr in ("name", "slug", "code"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    return ""


def _concept_displayable(concept: Any, viewer_tenant_id: UUID | None) -> bool:
    """The read-side twin of :func:`require_visible_concept`: a referenced
    concept may be displayed to the caller only when it is a system row (or
    the caller's own tenant) and not soft-deleted."""
    if concept is None:
        return False
    if concept.deleted_at is not None:
        return False
    return concept.tenant_id is None or concept.tenant_id == viewer_tenant_id


def serialize_catalog_row(obj: Any, viewer_tenant_id: UUID | None = None) -> dict:
    """``to_dict()`` with the visibility projection on the class reference.

    ``class_concept_slug`` / ``class_concept_name`` are emitted only when the
    eagerly-loaded ``class_concept`` passes :func:`_concept_displayable`;
    otherwise they project to ``None`` while ``class_concept_id`` keeps its
    stored value — a link written before the visibility projection existed is
    never echoed to a caller that cannot see it. Shared by the medication / allergy / vaccine /
    anatomy response shapes (one row shape, one rule).
    """
    data = obj.to_dict()
    concept = getattr(obj, "class_concept", None)
    if concept is not None and not _concept_displayable(concept, viewer_tenant_id):
        data["class_concept_slug"] = None
        data["class_concept_name"] = None
    return data


# ---------------------------------------------------------------------------
# Shared delete / promote (generic across the five scope-tiered catalogs)
# ---------------------------------------------------------------------------


async def delete_catalog_item(
    db: AsyncSession,
    actor: Any,
    item_id: UUID,
    *,
    model: type,
    catalog_type: str,
    soft_delete: bool = False,
) -> bool:
    """Delete one catalog row (scope + ownership gated). ``False`` if the row
    is missing or invisible to the caller."""
    obj = await _load_visible(db, model, item_id, actor.tenant_id, soft_delete=soft_delete)
    if obj is None:
        return False
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        obj.scope,
        item_created_by=obj.created_by,
        actor_user_id=actor.user_id,
    )
    snapshot_id, snapshot_name = obj.id, _label(obj)
    await db.delete(obj)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await _audit_snapshot(db, actor, catalog_type, snapshot_id, snapshot_name, "delete")
    return True


async def promote_catalog_item(
    db: AsyncSession,
    actor: Any,
    item_id: UUID,
    target_scope: str,
    *,
    model: type,
    catalog_type: str,
    serialize: Any,
    soft_delete: bool = False,
) -> dict[str, Any] | None:
    """Transition a row's scope tier (role-gated). ``None`` if missing.

    On promote-to-system the ``tenant_id`` is cleared; on demote-to-tenant it
    is set to the actor's tenant. A slug duplicate at the target tier raises
    :class:`~app.catalogs.policy.CatalogConflict` (→ 409); any other
    constraint the tier change violates (a colliding ``code``/``seed_key``
    per-scope unique, an FK) is translated after rollback into the typed 409
    / 400 — never a raw 500.
    """
    obj = await _load_visible(db, model, item_id, actor.tenant_id, soft_delete=soft_delete)
    if obj is None:
        return None
    target = CatalogScope(target_scope)
    from_scope = obj.scope.value if obj.scope is not None else None
    DEFAULT_CATALOG_POLICY.check_promote(actor.role, obj.scope, target)
    await _check_slug_collision(db, model, obj, target, actor.tenant_id)
    if target.value == "system":
        obj.tenant_id = None
    elif target.value == "tenant" and obj.tenant_id is None:
        obj.tenant_id = actor.tenant_id
    obj.scope = target
    await mark_user_modified_for_model(db, model, obj.id)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(obj)
    await _audit(
        db, actor, catalog_type, obj, "promote", from_scope=from_scope, to_scope=target.value
    )
    out = serialize(db, obj, viewer_tenant_id=actor.tenant_id)
    if inspect.isawaitable(out):
        out = await out
    return out


async def _check_slug_collision(
    db: AsyncSession, model: type, obj: Any, target: CatalogScope, actor_tenant_id: UUID | None
) -> None:
    """Raise CatalogConflict if another row at the target tier owns the slug."""
    slug_col = getattr(model, "slug", None)
    if slug_col is None or not getattr(obj, "slug", None):
        return
    if obj.scope is not None and obj.scope.value == target.value:
        return
    target_tenant = None if target.value == "system" else actor_tenant_id
    tenant_pred = (
        model.tenant_id.is_(None) if target_tenant is None else model.tenant_id == target_tenant
    )
    stmt = select(model.id, model.name).where(slug_col == obj.slug, tenant_pred, model.id != obj.id)
    row = (await db.execute(stmt)).first()
    if row is not None:
        from app.catalogs.policy import CatalogConflict

        raise CatalogConflict(
            slug=obj.slug,
            existing_id=str(row[0]),
            existing_name=row[1],
            target_scope=target.value,
        )


# ---------------------------------------------------------------------------
# Medication
# ---------------------------------------------------------------------------


async def create_medication(db: AsyncSession, actor: Any, data: MedicationCatalogCreate) -> dict:
    obj = MedicationCatalog(
        **data.model_dump(exclude={"class_concept_id", "class_concept_slug"}),
        class_concept_id=await _resolve_class_concept(
            db,
            tenant_id=actor.tenant_id,
            class_concept_id=data.class_concept_id,
            class_concept_slug=data.class_concept_slug,
        ),
    )
    DEFAULT_CATALOG_POLICY.assign_create_scope(actor.role, obj, actor.tenant_id, actor.user_id)
    assert_valid_fhir(obj)
    db.add(obj)
    try:
        await db.flush()
        await mark_user_modified_for_model(db, MedicationCatalog, obj.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(obj)
    await _audit(db, actor, "medication", obj, "create")
    return serialize_catalog_row(obj, actor.tenant_id)


async def update_medication(
    db: AsyncSession, actor: Any, item_id: UUID, data: MedicationCatalogUpdate
) -> dict | None:
    """Patch one medication row.

    Reference precedence (uniform across every catalog type): a sent
    ``class_concept_id`` wins over ``class_concept_slug`` — an explicit
    ``null`` id clears the link and any slug sent alongside is ignored; a
    bare slug re-resolves tenant-explicitly (unknown → the typed 400).
    """
    obj = await _load_visible(db, MedicationCatalog, item_id, actor.tenant_id)
    if obj is None:
        return None
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        obj.scope,
        item_created_by=obj.created_by,
        actor_user_id=actor.user_id,
    )
    fields = data.model_dump(exclude_unset=True)
    if "class_concept_id" in fields:
        fields["class_concept_id"] = await require_visible_concept(
            db, fields.get("class_concept_id"), actor.tenant_id
        )
        fields.pop("class_concept_slug", None)
    elif "class_concept_slug" in fields:
        slug_value = fields.pop("class_concept_slug")
        fields["class_concept_id"] = await _resolve_class_concept(
            db,
            tenant_id=actor.tenant_id,
            class_concept_id=None,
            class_concept_slug=slug_value,
        )
    for key, value in fields.items():
        setattr(obj, key, value)
    assert_valid_fhir(obj)
    await mark_user_modified_for_model(db, MedicationCatalog, obj.id)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(obj)
    await _audit(db, actor, "medication", obj, "update")
    return serialize_catalog_row(obj, actor.tenant_id)


async def delete_medication(db: AsyncSession, actor: Any, item_id: UUID) -> bool:
    return await delete_catalog_item(
        db, actor, item_id, model=MedicationCatalog, catalog_type="medication"
    )


# ---------------------------------------------------------------------------
# Allergy
# ---------------------------------------------------------------------------


async def create_allergy(db: AsyncSession, actor: Any, data: AllergyCatalogCreate) -> dict:
    obj = AllergyCatalog(
        **data.model_dump(exclude={"class_concept_id", "class_concept_slug"}),
        class_concept_id=await _resolve_class_concept(
            db,
            tenant_id=actor.tenant_id,
            class_concept_id=data.class_concept_id,
            class_concept_slug=data.class_concept_slug,
        ),
    )
    DEFAULT_CATALOG_POLICY.assign_create_scope(actor.role, obj, actor.tenant_id, actor.user_id)
    assert_valid_fhir(obj)
    db.add(obj)
    try:
        await db.flush()
        await mark_user_modified_for_model(db, AllergyCatalog, obj.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(obj)
    await _audit(db, actor, "allergy", obj, "create")
    return serialize_catalog_row(obj, actor.tenant_id)


async def update_allergy(
    db: AsyncSession, actor: Any, item_id: UUID, data: AllergyCatalogUpdate
) -> dict | None:
    """Patch one allergy row.

    Reference precedence matches :func:`update_medication`: a sent
    ``class_concept_id`` wins — an explicit ``null`` clears the link and any
    slug sent alongside is ignored; a bare slug re-resolves tenant-explicitly.
    """
    obj = await _load_visible(db, AllergyCatalog, item_id, actor.tenant_id)
    if obj is None:
        return None
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        obj.scope,
        item_created_by=obj.created_by,
        actor_user_id=actor.user_id,
    )
    fields = data.model_dump(exclude_unset=True)
    if "class_concept_id" in fields:
        fields["class_concept_id"] = await require_visible_concept(
            db, fields.get("class_concept_id"), actor.tenant_id
        )
        fields.pop("class_concept_slug", None)
    elif "class_concept_slug" in fields:
        slug_value = fields.pop("class_concept_slug")
        fields["class_concept_id"] = await _resolve_class_concept(
            db,
            tenant_id=actor.tenant_id,
            class_concept_id=None,
            class_concept_slug=slug_value,
        )
    for key, value in fields.items():
        setattr(obj, key, value)
    assert_valid_fhir(obj)
    await mark_user_modified_for_model(db, AllergyCatalog, obj.id)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(obj)
    await _audit(db, actor, "allergy", obj, "update")
    return serialize_catalog_row(obj, actor.tenant_id)


async def delete_allergy(db: AsyncSession, actor: Any, item_id: UUID) -> bool:
    return await delete_catalog_item(
        db, actor, item_id, model=AllergyCatalog, catalog_type="allergy"
    )


# ---------------------------------------------------------------------------
# Vaccine — slug derived server-side (display-only, per-tenant unique)
# ---------------------------------------------------------------------------


def _derive_slug(name: str, prefix: str = "vaccine") -> str:
    """Derive a display slug from the name (lowercase, hyphen runs).

    A non-Latin name that reduces to nothing falls back to a deterministic
    ``<prefix>-<digest>`` (sha1 of the name) instead of the bare prefix, so
    two distinct non-Latin names can never collide on the fallback while the
    same name still collides predictably (→ 409 duplicate). The ``prefix``
    namespaces the fallback per catalog type (``vaccine-…`` / ``biomarker-…``)
    so equal non-Latin names across types never share a slug."""
    import hashlib
    import re

    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if cleaned:
        return cleaned
    digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


async def create_vaccine(db: AsyncSession, actor: Any, data: VaccineCatalogCreate) -> dict:
    scope = DEFAULT_CATALOG_POLICY.create_scope(actor.role)
    slug = _derive_slug(data.name)
    if await _slug_taken(db, VaccineCatalog, slug, actor.tenant_id, scope):
        raise CatalogUniqueViolation(
            "slug", slug, message=f"vaccine with slug '{slug}' already exists"
        )
    obj = VaccineCatalog(
        **data.model_dump(exclude={"class_concept_id", "class_concept_slug"}),
        slug=slug,
        class_concept_id=await _resolve_class_concept(
            db,
            tenant_id=actor.tenant_id,
            class_concept_id=data.class_concept_id,
            class_concept_slug=data.class_concept_slug,
            kind=ConceptKind.VACCINE_CLASS,
        ),
    )
    DEFAULT_CATALOG_POLICY.assign_create_scope(actor.role, obj, actor.tenant_id, actor.user_id)
    assert_valid_fhir(obj)
    db.add(obj)
    try:
        await db.flush()
        await mark_user_modified_for_model(db, VaccineCatalog, obj.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(obj)
    await _audit(db, actor, "vaccine", obj, "create")
    return serialize_catalog_row(obj, actor.tenant_id)


async def _slug_taken(
    db: AsyncSession, model: type, slug: str, tenant_id: UUID | None, scope: CatalogScope
) -> bool:
    """Scope-tier slug collision check, mirroring the per-scope unique index
    exactly (``COALESCE(tenant_id, sentinel)``): a system row collides with a
    system row, a tenant row with a tenant row — never across tiers (tenant
    overlays of system slugs are legal, ADR 0055)."""
    tenant_pred = (
        model.tenant_id.is_(None) if scope is CatalogScope.SYSTEM else model.tenant_id == tenant_id
    )
    stmt = select(model.id).where(model.slug == slug, tenant_pred)
    return (await db.execute(stmt)).first() is not None


async def update_vaccine(
    db: AsyncSession, actor: Any, item_id: UUID, data: VaccineCatalogUpdate
) -> dict | None:
    """Patch one vaccine row.

    Reference precedence matches :func:`update_medication`: a sent
    ``class_concept_id`` wins — an explicit ``null`` clears the link and any
    slug sent alongside is ignored; a bare slug re-resolves tenant-explicitly
    against vaccine-class concepts.
    """
    obj = await _load_visible(db, VaccineCatalog, item_id, actor.tenant_id)
    if obj is None:
        return None
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        obj.scope,
        item_created_by=obj.created_by,
        actor_user_id=actor.user_id,
    )
    fields = data.model_dump(exclude_unset=True)
    if "class_concept_id" in fields:
        fields["class_concept_id"] = await require_visible_concept(
            db, fields.get("class_concept_id"), actor.tenant_id
        )
        fields.pop("class_concept_slug", None)
    elif "class_concept_slug" in fields:
        slug_value = fields.pop("class_concept_slug")
        fields["class_concept_id"] = await _resolve_class_concept(
            db,
            tenant_id=actor.tenant_id,
            class_concept_id=None,
            class_concept_slug=slug_value,
            kind=ConceptKind.VACCINE_CLASS,
        )
    for key, value in fields.items():
        setattr(obj, key, value)
    assert_valid_fhir(obj)
    await mark_user_modified_for_model(db, VaccineCatalog, obj.id)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(obj)
    await _audit(db, actor, "vaccine", obj, "update")
    return serialize_catalog_row(obj, actor.tenant_id)


async def delete_vaccine(db: AsyncSession, actor: Any, item_id: UUID) -> bool:
    return await delete_catalog_item(
        db, actor, item_id, model=VaccineCatalog, catalog_type="vaccine"
    )


# ---------------------------------------------------------------------------
# Anatomy — delegates persistence to the anatomy service (kind-sync parity)
# ---------------------------------------------------------------------------


def serialize_anatomy(obj: AnatomyStructure) -> dict:
    return obj.to_dict()


async def create_anatomy(db: AsyncSession, actor: Any, data: AnatomyStructureCreate) -> dict:
    from app.services.anatomy import anatomy_service

    if (
        await anatomy_service.get_anatomy_structure_by_id_or_slug(db, data.slug, actor.tenant_id)
        is not None
    ):
        raise CatalogUniqueViolation(
            "slug", data.slug, message=f"structure with slug '{data.slug}' already exists"
        )
    try:
        structure = await anatomy_service.create_anatomy_structure(
            db,
            data,
            role=actor.role,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await _audit(db, actor, "anatomy", structure, "create")
    return serialize_catalog_row(structure, actor.tenant_id)


async def update_anatomy(
    db: AsyncSession, actor: Any, item_id: UUID, data: AnatomyStructureUpdate
) -> dict | None:
    from app.services.anatomy import anatomy_service

    structure = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, str(item_id), actor.tenant_id
    )
    if structure is None:
        return None
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        structure.scope,
        item_created_by=structure.created_by,
        actor_user_id=actor.user_id,
    )
    try:
        updated = await anatomy_service.update_anatomy_structure(
            db, structure, data, tenant_id=actor.tenant_id
        )
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await _audit(db, actor, "anatomy", updated, "update")
    return serialize_catalog_row(updated, actor.tenant_id)


async def delete_anatomy(db: AsyncSession, actor: Any, item_id: UUID) -> bool:
    return await delete_catalog_item(
        db, actor, item_id, model=AnatomyStructure, catalog_type="anatomy"
    )


# ---------------------------------------------------------------------------
# Concept — delegates to ConceptService (the single taxonomy write authority:
# kind-sync, retire-on-edges, curated-taxonomy RBAC, audit)
# ---------------------------------------------------------------------------


async def serialize_concept(
    db: AsyncSession, concept, viewer_tenant_id: UUID | None = None
) -> dict:
    """Full concept row (``to_dict`` + the resolved ``parent_slug``).

    ``parent_slug`` resolves only when the parent is visible to the viewer
    (system row or the viewer's tenant, ``deleted_at IS NULL``); a link
    written before the visibility projection existed projects to ``None``
    while ``parent_id`` keeps its stored value.
    """
    data = concept.to_dict()
    if concept.parent_id is not None:
        parent_slug = (
            await db.execute(
                select(Concept.slug).where(
                    Concept.id == concept.parent_id,
                    or_(Concept.tenant_id.is_(None), Concept.tenant_id == viewer_tenant_id),
                    Concept.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        data["parent_slug"] = parent_slug
    else:
        data["parent_slug"] = None
    return data


def _concept_kinds(data: ConceptCreate) -> list[ConceptKind]:
    resolved = [ConceptKind(k) for k in (data.kinds or [])]
    if data.kind is not None and data.kind not in [k.value for k in resolved]:
        resolved.append(ConceptKind(data.kind))
    if data.primary_kind is not None and data.primary_kind not in [k.value for k in resolved]:
        resolved.append(ConceptKind(data.primary_kind))
    if not resolved:
        raise ValueError("at least one kind is required")
    return resolved


async def create_concept(db: AsyncSession, actor: Any, data: ConceptCreate) -> dict:
    svc = ConceptService(db)
    tenant_id = actor.tenant_id if data.tenant_scoped else None
    # An explicit parent must be visible (system or the actor's tenant) —
    # never another tenant's private concept (→ typed 400).
    parent_id = await require_visible_concept(db, data.parent_id, actor.tenant_id)
    try:
        concept = await svc.create_concept(
            slug=data.slug,
            name=data.name,
            kinds=_concept_kinds(data),
            tenant_id=tenant_id,
            role=actor.role,
            description=data.description,
            parent_id=parent_id,
            coding_system=data.coding_system,
            code=data.code,
            aliases=data.aliases,
            icon=data.icon,
            color=data.color,
            display_order=data.display_order,
            meta_data=data.meta_data,
            created_by=actor.user_id,
            actor=actor,
        )
        await db.flush()
        await mark_user_modified_for_model(db, Concept, concept.id)
        await db.commit()
    except PermissionError as exc:
        raise CatalogPermissionDenied(str(exc)) from exc
    except CatalogUniqueViolation:
        raise
    return await serialize_concept(db, concept, actor.tenant_id)


async def update_concept(
    db: AsyncSession, actor: Any, item_id: UUID, data: ConceptUpdate
) -> dict | None:
    svc = ConceptService(db)
    fields = data.model_dump(exclude_unset=True)
    # An explicit parent must be visible (system or the actor's tenant); a
    # bogus one is a typed 400 before it can become an FK 500.
    if fields.get("parent_id") is not None:
        fields["parent_id"] = await require_visible_concept(
            db, fields["parent_id"], actor.tenant_id
        )
    try:
        concept = await svc.update_concept(
            item_id, actor.tenant_id, actor.role, actor=actor, **fields
        )
        await mark_user_modified_for_model(db, Concept, concept.id)
        await db.commit()
    except PermissionError as exc:
        raise CatalogPermissionDenied(str(exc)) from exc
    except CatalogUniqueViolation:
        raise
    except ConceptNotFoundError:
        await db.rollback()
        return None
    except IntegrityError as exc:
        # The ConceptService update flush is unguarded (shared service) —
        # translate here so no constraint violation escapes as a 500.
        await db.rollback()
        raise _translate_integrity(exc) from exc
    await db.refresh(concept)
    return await serialize_concept(db, concept, actor.tenant_id)


async def delete_concept(db: AsyncSession, actor: Any, item_id: UUID) -> bool:
    """Retire-or-soft-delete via ConceptService (never a hard delete: the
    polymorphic graph must not dangle)."""
    svc = ConceptService(db)
    try:
        await svc.delete_concept(item_id, actor.tenant_id, actor.role, actor=actor)
        await db.commit()
    except PermissionError as exc:
        raise CatalogPermissionDenied(str(exc)) from exc
    except ConceptNotFoundError:
        await db.rollback()
        return False
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    return True


# ---------------------------------------------------------------------------
# Biomarker — the endpoint-owned logic moved here (unit symbol → id,
# class-concept id/slug → class concept, reference_ranges, allowed_states,
# telemetry migration trigger, name → slug when the client omits it); the
# domain endpoint and the meta-layer adapter both call these functions.
# ---------------------------------------------------------------------------


async def reload_biomarker(db: AsyncSession, bio_id: UUID) -> BiomarkerDefinition:
    """Re-fetch a definition with all relationships eager-loaded (post-commit
    reads would otherwise hit lazy-load greenlet errors in async context)."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(BiomarkerDefinition)
        .options(
            selectinload(BiomarkerDefinition.allowed_states).selectinload(
                BiomarkerAllowedState.state
            ),
            selectinload(BiomarkerDefinition.reference_ranges),
            selectinload(BiomarkerDefinition.preferred_unit),
            selectinload(BiomarkerDefinition.class_concept),
        )
        .where(BiomarkerDefinition.id == bio_id)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one()


def serialize_biomarker(bio: BiomarkerDefinition, viewer_tenant_id: UUID | None = None) -> dict:
    """The single biomarker response shape (list/get/create/update agree).

    Requires the relationships to be loaded (``selectin`` on the model, or
    :func:`reload_biomarker` after a commit). ``viewer_tenant_id`` drives the
    class-concept display projection: ``class_concept_slug`` /
    ``class_concept_name`` are emitted only when the referenced concept is a
    system row or the viewer's own tenant and not soft-deleted — otherwise
    ``None`` (``class_concept_id`` keeps its stored value). ``None`` viewer
    means a system-only viewer; every caller threads the request's tenant.
    """
    concept = bio.class_concept
    concept_visible = _concept_displayable(concept, viewer_tenant_id)
    return {
        "id": str(bio.id),
        "slug": bio.slug,
        "seed_key": bio.seed_key,
        "coding_system": bio.coding_system.value if bio.coding_system else None,
        "code": bio.code,
        "name": bio.name,
        "description": bio.description,
        "class_concept_id": str(bio.class_concept_id) if bio.class_concept_id else None,
        "class_concept_slug": concept.slug if concept_visible else None,
        "class_concept_name": concept.name if concept_visible else None,
        "aliases": bio.aliases or [],
        "preferred_unit_id": str(bio.preferred_unit_id) if bio.preferred_unit_id else None,
        "preferred_unit_symbol": bio.preferred_unit.symbol if bio.preferred_unit else None,
        "info": bio.info,
        "reference_range_min": bio.reference_range_min,
        "reference_range_max": bio.reference_range_max,
        "reference_ranges": [
            {
                "id": str(rr.id),
                "biomarker_id": str(rr.biomarker_id),
                "sex": rr.sex.value if rr.sex else None,
                "age_min": rr.age_min,
                "age_max": rr.age_max,
                "unit_id": str(rr.unit_id) if rr.unit_id else None,
                "low": rr.low,
                "high": rr.high,
                "text": rr.text,
                "applies_to": rr.applies_to,
            }
            for rr in (bio.reference_ranges or [])
        ],
        "is_telemetry": bio.is_telemetry,
        "value_type": bio.value_type.value if bio.value_type else "quantity",
        "supports_multi_state": bio.supports_multi_state or False,
        "allowed_states": [
            {
                "state_id": str(als.state.id),
                "state_slug": als.state.slug,
                "code": als.state.code,
                "system": als.state.system,
                "display": als.state.display,
                "is_normal": als.is_normal,
                "sort_order": als.sort_order,
            }
            for als in (bio.allowed_states or [])
            if als.state is not None
        ],
        "meta_data": bio.meta_data,
        "scope": bio.scope.value if bio.scope else "system",
        "tenant_id": str(bio.tenant_id) if bio.tenant_id else None,
        "created_by": str(bio.created_by) if bio.created_by else None,
    }


async def resolve_allowed_states(
    db: AsyncSession, specs: list[AllowedStateSpec]
) -> list[BiomarkerAllowedState]:
    """Resolve slug-keyed state specs to join rows; ValueError (→ 400) on an
    unknown slug (the universal state catalog is not tenant-scoped)."""
    if not specs:
        return []
    slugs = [s.state_slug for s in specs]
    rows = (
        (await db.execute(select(BiomarkerState).where(BiomarkerState.slug.in_(slugs))))
        .scalars()
        .all()
    )
    by_slug = {r.slug: r for r in rows}
    missing = [s for s in slugs if s not in by_slug]
    if missing:
        raise ValueError(f"Unknown biomarker_state slug(s): {missing}")
    return [
        BiomarkerAllowedState(
            state_id=by_slug[spec.state_slug].id,
            is_normal=spec.is_normal,
            sort_order=spec.sort_order,
        )
        for spec in specs
    ]


async def _resolve_unit_id(
    db: AsyncSession,
    unit_id: UUID | None,
    unit_symbol: str | None,
) -> UUID | None:
    """An explicit unit id wins (validated to exist); a symbol must resolve.
    Unknown either way raises ValueError (→ 400) — never a silent drop and
    never a raw FK 500. Units are global vocabulary (no tenant scoping)."""
    if unit_id is not None:
        unit = (await db.execute(select(Unit).where(Unit.id == unit_id))).scalar_one_or_none()
        if unit is None:
            raise ValueError(f"Unknown unit id '{unit_id}'")
        return unit.id
    if unit_symbol:
        unit = (
            await db.execute(select(Unit).where(Unit.symbol == unit_symbol))
        ).scalar_one_or_none()
        if unit is None:
            raise ValueError(f"Unknown unit symbol '{unit_symbol}'")
        return unit.id
    return None


async def _resolve_biomarker_class(
    db: AsyncSession, actor: Any, data: BiomarkerCreate | BiomarkerUpdate
) -> UUID | None:
    """class_concept_id wins (visibility-validated); then the class slug, which
    must resolve (typed 400 otherwise). Both lookups are tenant-explicit
    (system rows + the actor's tenant)."""
    if data.class_concept_id is not None:
        return await require_visible_concept(db, data.class_concept_id, actor.tenant_id)
    if getattr(data, "class_concept_slug", None):
        resolved = await resolve_concept_by_slug(
            db, data.class_concept_slug, ConceptKind.BIOMARKER_CLASS, tenant_id=actor.tenant_id
        )
        if resolved is None:
            raise CatalogReferenceError("class concept")
        return resolved
    return None


async def _biomarker_class_for_update(
    db: AsyncSession, actor: Any, fields: dict, sent_fields: set[str]
) -> tuple[bool, UUID | None]:
    """Update-path class resolution.

    Returns ``(touched, value)``: ``touched=False`` when the payload doesn't
    touch the class link at all (the stored link stays); otherwise the
    resolved id, where ``None`` means "explicitly clear the link"
    (``model_fields_set`` distinguishes "not sent" from "explicit null" —
    ``class_concept_id: null`` / ``class_concept_slug: null`` clear it).

    Precedence: ``class_concept_id`` wins over ``class_concept_slug`` (the
    fields-set semantics every other catalog type now mirrors — a sent null
    id clears the link and any slug sent alongside is ignored); an
    unknown/invisible id or slug raises ``CatalogReferenceError`` (→ 400).
    """
    if "class_concept_id" in sent_fields:
        value = await require_visible_concept(db, fields.get("class_concept_id"), actor.tenant_id)
        return True, value
    if "class_concept_slug" in sent_fields:
        slug = fields.get("class_concept_slug")
        if not slug:
            return True, None  # explicit empty slug → clear the link
        resolved = await resolve_concept_by_slug(
            db, slug, ConceptKind.BIOMARKER_CLASS, tenant_id=actor.tenant_id
        )
        if resolved is None:
            raise CatalogReferenceError("class concept")
        return True, resolved
    return False, None


def _raise_state_telemetry_guard(existing: BiomarkerDefinition, data: BiomarkerUpdate) -> None:
    """STATE biomarkers cannot become telemetry (telemetry_data.value is NOT
    NULL); the schema catches the simultaneous case, this catches the toggle on
    an already-STATE row."""
    from app.models.enums import BiomarkerValueType

    new_is_telemetry = data.is_telemetry
    if new_is_telemetry is True and existing.value_type == BiomarkerValueType.STATE:
        raise ValueError(
            "STATE biomarkers cannot be telemetry (categorical values have "
            "nowhere to go on telemetry_data.value Float NOT NULL)."
        )


async def _biomarker_slug(
    db: AsyncSession, data: BiomarkerCreate, tenant_id: UUID | None, scope: CatalogScope
) -> str:
    """Resolve the create slug (server-owned identity, plan D8).

    An explicit non-empty slug is honored verbatim — a duplicate still hits
    the per-scope unique index and translates to the typed 409 (never a
    silent suffix of a caller-chosen identity). An omitted/empty slug is
    derived from the name with the deterministic vaccine algorithm (biomarker
    fallback prefix). The derived base is capped at 77 chars so a
    ``-2``…``-99`` auto-suffix keeps the final slug within the 80-char
    identifier cap, and it is made unique per tenant scope with that
    auto-suffix (``-2``, ``-3``, …): the browse dialog and HITL proposals
    submit prefilled names without a slug.
    """
    explicit = (data.slug or "").strip()
    if explicit:
        return explicit
    base = _derive_slug(data.name, prefix="biomarker")[:77].rstrip("-") or "biomarker"
    slug = base
    suffix = 2
    while await _slug_taken(db, BiomarkerDefinition, slug, tenant_id, scope):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


async def create_biomarker(db: AsyncSession, actor: Any, data: BiomarkerCreate) -> dict:
    from app.models.enums import BiomarkerValueType

    unit_id = await _resolve_unit_id(db, data.preferred_unit_id, data.preferred_unit_symbol)
    class_concept_id = await _resolve_biomarker_class(db, actor, data)
    scope = DEFAULT_CATALOG_POLICY.create_scope(actor.role)
    slug = await _biomarker_slug(db, data, actor.tenant_id, scope)
    bio = BiomarkerDefinition(
        slug=slug,
        name=data.name,
        coding_system=data.coding_system,
        code=data.code,
        description=data.description,
        class_concept_id=class_concept_id,
        aliases=data.aliases or [],
        info=data.info,
        reference_range_min=data.reference_range_min,
        reference_range_max=data.reference_range_max,
        is_telemetry=bool(data.is_telemetry),
        preferred_unit_id=unit_id,
        value_type=data.value_type,
        supports_multi_state=bool(data.supports_multi_state),
    )
    DEFAULT_CATALOG_POLICY.assign_create_scope(actor.role, bio, actor.tenant_id, actor.user_id)
    db.add(bio)
    try:
        # Flush inside the guard: a bogus preferred_unit_id / class_concept_id
        # that slipped past pre-validation is a typed 400, never a raw 500.
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    if data.value_type == BiomarkerValueType.STATE:
        for row in await resolve_allowed_states(db, data.allowed_states):
            row.biomarker_id = bio.id
            db.add(row)
    for rr in data.reference_ranges:
        db.add(
            BiomarkerReferenceRange(
                biomarker_id=bio.id,
                **rr.model_dump(),
                created_by=actor.user_id,
                updated_by=actor.user_id,
            )
        )
    await mark_user_modified_for_model(db, BiomarkerDefinition, bio.id)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    bio = await reload_biomarker(db, bio.id)
    await _audit(db, actor, "biomarker", bio, "create")
    return serialize_biomarker(bio, viewer_tenant_id=actor.tenant_id)


async def update_biomarker(
    db: AsyncSession, actor: Any, item_id: UUID, data: BiomarkerUpdate
) -> dict | None:
    from sqlalchemy import delete as sa_delete
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.enums import BiomarkerValueType

    bio = await _load_visible(db, BiomarkerDefinition, item_id, actor.tenant_id)
    if bio is None:
        return None
    DEFAULT_CATALOG_POLICY.check_modify(
        actor.role,
        bio.scope,
        item_created_by=bio.created_by,
        actor_user_id=actor.user_id,
    )

    prior_is_telemetry = bio.is_telemetry
    new_allowed_states = data.allowed_states

    _raise_state_telemetry_guard(bio, data)

    fields = data.model_dump(exclude_unset=True)
    sent_fields = data.model_fields_set
    unit_id = await _resolve_unit_id(db, data.preferred_unit_id, data.preferred_unit_symbol)
    class_touched, class_concept_id = await _biomarker_class_for_update(
        db, actor, fields, sent_fields
    )

    # Handled explicitly below / resolved above — never setattr them raw.
    fields.pop("allowed_states", None)
    fields.pop("class_concept_slug", None)
    fields.pop("preferred_unit_symbol", None)
    # An explicit null (or a resolved value) is persisted — "not sent" and
    # "explicit null" are distinguished via model_fields_set above, so a
    # reference can actually be cleared (ADR 0055 Phase-3: no silent drop).
    if "preferred_unit_id" in sent_fields or "preferred_unit_symbol" in sent_fields:
        fields["preferred_unit_id"] = unit_id
    if class_touched:
        fields["class_concept_id"] = class_concept_id

    new_is_telemetry = fields.get("is_telemetry")
    needs_migration = new_is_telemetry is not None and prior_is_telemetry != new_is_telemetry

    for key, value in fields.items():
        setattr(bio, key, value)

    if new_allowed_states is not None:
        if bio.value_type != BiomarkerValueType.STATE:
            raise ValueError("allowed_states can only be set on STATE biomarkers")
        await db.execute(
            sa_delete(BiomarkerAllowedState).where(BiomarkerAllowedState.biomarker_id == bio.id)
        )
        for row in await resolve_allowed_states(db, new_allowed_states):
            row.biomarker_id = bio.id
            db.add(row)

    try:
        if needs_migration:
            meta = dict(bio.meta_data or {})
            meta["migration_status"] = "in_progress"
            meta["migration_progress"] = 0
            meta.pop("migration_error", None)
            bio.meta_data = meta
            flag_modified(bio, "meta_data")
        await mark_user_modified_for_model(db, BiomarkerDefinition, bio.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _translate_integrity(exc) from exc
    if needs_migration:
        from app.workers.tasks import migrate_biomarker_data

        migrate_biomarker_data.delay(str(bio.id), str(actor.tenant_id), bool(new_is_telemetry))
    bio = await reload_biomarker(db, bio.id)
    await _audit(db, actor, "biomarker", bio, "update")
    return serialize_biomarker(bio, viewer_tenant_id=actor.tenant_id)


async def delete_biomarker(db: AsyncSession, actor: Any, item_id: UUID) -> bool:
    return await delete_catalog_item(
        db,
        actor,
        item_id,
        model=BiomarkerDefinition,
        catalog_type="biomarker",
    )
