"""Curated seed identity — the upsert-by-identity helper (ADR 0055).

One home for the lookup + create/update rules the seeders share:

* :func:`require_seed_key` — the mandatory qualified ``seed_key`` of a seed
  item, validated against the ``<type>:<kebab-key>`` form (and its type
  prefix) before any row is touched.
* :func:`identity_id` — the deterministic row id: ``uuid5`` derived from the
  seed key, or an explicit ``id`` from the item (pack/import use) which wins
  at creation.
* :func:`find_identity_row` — find a curated row **by id first, then by
  ``seed_key``**, never by name or slug. Every lookup is scope-pinned
  (where the model carries tenancy): ``tenant_id IS NULL`` for the system
  lane, ``tenant_id == <tenant>`` for a tenant overlay pack — a tenant row
  sharing a system seed key is never found, updated or shadowed by the
  system lane, and vice versa (ADR 0055 per-tenant-scope uniqueness).
* :func:`apply_values` — write mutable fields onto an existing row, reporting
  whether anything really changed; JSONB fields are ``flag_modified``-marked
  so in-place mutations flush.
* :func:`create_identity_row` / :func:`upsert_by_identity` — build a new row
  with scope stamps (system: ``tenant_id=None``/``scope='system'``; tenant:
  ``tenant_id=<tenant>``/``scope='tenant'``, no ``created_by``;
  ``created_at``/``created_by`` come from the mixins on insert only) or
  compose find + update-in-place + create.

Row ids stay deterministic per scope: a tenant overlay row gets
``uuid5(NS, "<tenant>/<type>:<key>")`` (:func:`identity_id` with
``tenant_id``) so it can reuse a system ``seed_key`` without colliding with
the system row's id.

An existing row's primary key is never retargeted: an explicit ``id`` applies
at creation only; an explicit id that already belongs to a **different** seed
key raises :class:`SeedIdentityConflict`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.catalog_identity import SEED_KEY_NAMESPACE, seed_key_uuid, validate_seed_key

logger = logging.getLogger(__name__)


class SeedIdentityConflict(ValueError):
    """An explicit seed ``id`` collides with a row of a different seed key."""


def item_label(item: dict[str, Any]) -> str:
    """The best readable label of a seed item, for logs and error messages."""
    return item.get("slug") or item.get("name") or item.get("symbol") or "?"


def require_seed_key(item: dict[str, Any], catalog_type: str) -> str:
    """The mandatory, type-matching qualified seed key of a seed item.

    Raises:
        ValueError: when the item carries no ``seed_key`` or one whose
            qualified form (or ``<type>:`` prefix) does not match.
    """
    qualified = item.get("seed_key")
    if not qualified:
        raise ValueError(
            f"seed item {item_label(item)!r} is missing its mandatory 'seed_key' "
            f"(expected '{catalog_type}:<kebab-key>')"
        )
    key_type, _ = validate_seed_key(qualified)
    if key_type != catalog_type:
        raise ValueError(
            f"seed item {item_label(item)!r} declares seed_key {qualified!r} but this stage "
            f"seeds '{catalog_type}:' keys"
        )
    return qualified


def identity_id(
    qualified_seed_key: str,
    explicit_id: uuid.UUID | str | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """The row id for a seed item — the explicit id wins over the derived one.

    System rows (``tenant_id=None``) derive ``uuid5(NS, "<type>:<key>")``;
    tenant overlay rows derive ``uuid5(NS, "<tenant>/<type>:<key>")`` so a
    tenant pack may reuse a system seed key without colliding with the
    system row's id (ADR 0055 per-tenant-scope uniqueness).

    Raises:
        ValueError: when the seed key is malformed or the explicit id is not
            a UUID.
    """
    catalog_type, key = validate_seed_key(qualified_seed_key)
    if explicit_id is None:
        if tenant_id is None:
            return seed_key_uuid(catalog_type, key)
        return uuid.uuid5(SEED_KEY_NAMESPACE, f"{tenant_id}/{catalog_type}:{key}")
    if isinstance(explicit_id, uuid.UUID):
        return explicit_id
    return uuid.UUID(str(explicit_id))


def scoped_seed_uuid(entity: str, body: str, *, tenant_id: uuid.UUID | None = None) -> uuid.UUID:
    """The deterministic id of a derived seed row (edges, vaccine PREVENTS).

    Mirrors :func:`seed_key_uuid` for the system scope and prefixes the
    tenant id for overlay rows so a tenant vaccine reusing a system key
    derives its own edge ids (ADR 0056 tenant packs).
    """
    if tenant_id is None:
        return seed_key_uuid(entity, body)
    return uuid.uuid5(SEED_KEY_NAMESPACE, f"{tenant_id}/{entity}:{body}")


def has_seed_key_column(model) -> bool:
    """Whether the model stores ``seed_key`` (determines the fallback lookup)."""
    return hasattr(model, "seed_key")


def _scope_filters(model, tenant_id: uuid.UUID | None = None) -> list[Any]:
    """The lookup filters that pin the query to the requested scope.

    ``tenant_id=None`` pins to system rows (``tenant_id IS NULL``); a tenant
    id pins to that tenant's rows — the two lanes never see each other.
    """
    if not hasattr(model, "tenant_id"):
        return []
    if tenant_id is None:
        return [model.tenant_id.is_(None)]
    return [model.tenant_id == tenant_id]


async def find_identity_row(
    session: AsyncSession,
    model,
    qualified_seed_key: str,
    *,
    tenant_id: uuid.UUID | None = None,
    explicit_id: uuid.UUID | str | None = None,
):
    """Find the curated row for a seed key — by id, then ``seed_key``.

    The id lookup matches the derived (or explicit) id; when the model stores
    ``seed_key`` a second lookup by key catches a row whose id predates the
    deterministic scheme. Lookups are scope-pinned: ``tenant_id=None`` finds
    system rows only, a tenant id finds that tenant's overlay rows — a
    tenant overlay sharing the key is never adopted by the system lane and
    never shadowed by it.

    Raises:
        SeedIdentityConflict: when the id-matched row belongs to a different
            seed key (the id is taken by another identity).
    """
    row_id = identity_id(qualified_seed_key, explicit_id, tenant_id=tenant_id)
    row = (
        await session.execute(
            select(model).where(model.id == row_id, *_scope_filters(model, tenant_id))
        )
    ).scalar_one_or_none()
    if row is not None:
        if has_seed_key_column(model) and row.seed_key not in (None, qualified_seed_key):
            raise SeedIdentityConflict(
                f"seed id {row_id} already belongs to seed_key {row.seed_key!r}, "
                f"not {qualified_seed_key!r}"
            )
        return row
    if has_seed_key_column(model):
        row = (
            await session.execute(
                select(model).where(
                    model.seed_key == qualified_seed_key, *_scope_filters(model, tenant_id)
                )
            )
        ).scalar_one_or_none()
    return row


def apply_values(row, values: dict[str, Any], json_fields: tuple[str, ...] = ()) -> bool:
    """Write mutable fields onto an existing row; ``True`` when anything changed.

    Only differing fields are assigned (an equal value never dirties the row),
    and JSONB assignments (``json_fields``) are ``flag_modified``-marked so a
    same-identity object assignment still flushes.
    """
    changed = False
    for field, value in values.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            if field in json_fields:
                flag_modified(row, field)
            changed = True
    return changed


def create_identity_row(
    model,
    qualified_seed_key: str,
    values: dict[str, Any],
    *,
    tenant_id: uuid.UUID | None = None,
    explicit_id: uuid.UUID | str | None = None,
):
    """Build a new curated row with the deterministic scope-pinned id.

    ``seed_key`` (where the model has the column) and the tenancy stamps
    (system: ``tenant_id=None``/``scope='system'``; tenant overlay:
    ``tenant_id=<tenant>``/``scope='tenant'``) are stamped here;
    ``created_at``/``created_by`` stay with the mixins (insert-only).
    """
    kwargs: dict[str, Any] = dict(values)
    if has_seed_key_column(model):
        kwargs["seed_key"] = qualified_seed_key
    if hasattr(model, "tenant_id"):
        kwargs["tenant_id"] = tenant_id
    if hasattr(model, "scope"):
        from app.models.enums import CatalogScope

        kwargs["scope"] = CatalogScope.SYSTEM if tenant_id is None else CatalogScope.TENANT
    return model(id=identity_id(qualified_seed_key, explicit_id, tenant_id=tenant_id), **kwargs)


async def upsert_by_identity(
    session: AsyncSession,
    model,
    qualified_seed_key: str,
    values: dict[str, Any],
    *,
    tenant_id: uuid.UUID | None = None,
    explicit_id: uuid.UUID | str | None = None,
    json_fields: tuple[str, ...] = (),
) -> tuple[Any, str]:
    """Find → update-in-place, or create with the deterministic id.

    Returns ``(row, action)`` with ``action`` in ``{"added", "updated",
    "unchanged"}`` — ``updated`` only when a mutable field really changed.
    An existing row's id is kept even when the item carries an explicit one;
    use :class:`SeedIdentityConflict` handling upstream to reject ids that
    belong to another identity. ``tenant_id`` pins the lane: ``None`` upserts
    system rows, a tenant id upserts that tenant's overlay rows.
    """
    row = await find_identity_row(
        session, model, qualified_seed_key, tenant_id=tenant_id, explicit_id=explicit_id
    )
    if row is None:
        row = create_identity_row(
            model, qualified_seed_key, values, tenant_id=tenant_id, explicit_id=explicit_id
        )
        session.add(row)
        return row, "added"
    changed = apply_values(row, values, json_fields)
    return row, "updated" if changed else "unchanged"


@dataclass(frozen=True)
class SeedIdentityHint:
    """A seed item's identity, keyed by its payload natural reference.

    ``qualified`` is the validated ``<type>:<key>`` string; ``explicit_id``
    is the optional item ``id``. Carried alongside a payload whose Pydantic
    models do not accept identity fields (``seed_key`` stays response-only
    on the write contract), so the same import code can drive both the
    identity-native seed path and the admin door.
    """

    qualified: str
    explicit_id: uuid.UUID | None = None
