"""The catalog service contract + supporting value types.

Every clinical catalog (anatomy, taxonomy, biomarkers, medications, allergies,
vaccines, diseases) conforms to :class:`CatalogServiceProtocol` so that the
``/catalogs`` meta-layer, the search dispatcher, the graph service, and the LLM
tools can treat them uniformly.

Phase 0 implements only the read paths (``list`` / ``get``). Write methods are
declared on the protocol so the contract is complete, but adapters raise
``NotImplementedError`` until Phase 1 wires them with the RBAC policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class CatalogServiceProtocol(Protocol):
    """Uniform CRUD + search contract for one catalog type.

    All methods receive the request's ``db`` session and the caller's ``tenant_id``
    (or full ``actor`` token for writes) and return JSON-serializable dicts (not
    ORM objects) so the meta-layer router needs no per-type Pydantic
    ``response_model``. Reads apply the standard
    ``or_(tenant_id == caller, tenant_id IS NULL)`` scoping so global canonical
    rows are visible to every tenant.

    Write methods enforce RBAC via the descriptor's
    :class:`~app.catalogs.policy.CatalogAccessPolicy` — they raise
    :class:`~app.catalogs.policy.CatalogPermissionDenied` (mapped to HTTP 403 by
    the global handler in ``main.py``) when the role/scope is insufficient.
    """

    async def list(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        *,
        search: Optional[str] = None,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return ``{"items": [dict, ...], "total": int}``.

        ``scope`` (when set) narrows to ``system`` | ``tenant`` | ``user``.
        """
        ...

    async def get(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        item_id: UUID,
    ) -> Optional[dict[str, Any]]:
        """Return one item as a dict, or ``None`` if not found/invisible."""
        ...

    async def create(
        self,
        db: AsyncSession,
        actor: "Any",
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create an item. The scope is derived from the creator's role
        (SYSTEM_ADMIN→system, ADMIN/MANAGER→tenant, USER→user). Any
        authenticated role may create (plan §1.2)."""
        ...

    async def update(
        self,
        db: AsyncSession,
        actor: "Any",
        item_id: UUID,
        payload: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Update one item. Enforced by scope + ownership (creator OR ADMIN
        for user-scope; ADMIN/MANAGER for tenant; SYSTEM_ADMIN for system).
        ``None`` if missing."""
        ...

    async def delete(
        self,
        db: AsyncSession,
        actor: "Any",
        item_id: UUID,
    ) -> bool:
        """Delete one item. Same scope/ownership gate as update.
        ``False`` if missing."""
        ...

    async def promote_scope(
        self,
        db: AsyncSession,
        actor: "Any",
        item_id: UUID,
        target_scope: str,
    ) -> Optional[dict[str, Any]]:
        """Transition an item's scope (plan §1.3). Role-gated: user↔tenant
        requires ADMIN/MANAGER; any transition involving system requires
        SYSTEM_ADMIN. ``None`` if the item is missing/out of scope."""
        ...


@dataclass(frozen=True)
class ConceptLink:
    """How a catalog row points into the taxonomy (``concepts.id``).

    Mirrors the established ``<role>_concept_id`` FK convention. ``None`` for
    catalogs that *are* concepts (diseases) or that have not yet been wired
    (medication/allergy gain this in Phase 2).
    """

    fk_column: str
    is_required: bool = False


@dataclass(frozen=True)
class CatalogUiMeta:
    """Frontend metadata for the unified ``/admin/catalogs`` workspace.

    The left rail of the workspace is driven entirely by ``GET /catalogs``
    returning this metadata — no hardcoded nav.
    """

    label_key: str
    icon: str
    color: str
    admin_route: str
