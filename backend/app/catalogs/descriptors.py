"""The :class:`CatalogDescriptor` — one immutable entry per catalog type.

A descriptor bundles everything the meta-layer, search dispatcher, graph
service, and FHIR facade need to treat a catalog uniformly:

- ``model`` — the SQLAlchemy ORM class (single source of truth for storage).
- ``service`` — a :class:`~app.catalogs.protocol.CatalogServiceProtocol` adapter.
- ``search_columns`` — text columns the generic listing runs ``ILIKE`` over.
- ``search_fts_columns`` — the full-text surface (defaults to ``search_columns``).
- ``search_trigram_columns`` / ``search_json_columns`` — per-type search extras.
- ``concept_link`` — the taxonomy FK (``class_concept_id`` convention).
- ``edge_endpoint_type`` — how this catalog participates in ``concept_edges``.
- ``resolver`` — bulk endpoint resolver for graph display payloads.
- ``rbac`` — the uniform access policy.
- ``ui`` — frontend metadata for the admin workspace.

Adding a catalog = one :meth:`CatalogRegistry.register` call (plus, for a new
table, one model + one resolver). Every consumer of the registry then gains the
new catalog automatically. The search surface declared here is also what the
migration lane indexes (:mod:`app.core.catalog_search_index`) — a searched
column is either declared here plus indexed, or it matches nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.catalogs.policy import CatalogAccessPolicy
from app.catalogs.protocol import CatalogServiceProtocol, CatalogUiMeta, ConceptLink
from app.models.enums import EdgeEndpointType


@dataclass(frozen=True)
class CatalogWriteContract:
    """The typed write contract for one catalog type (ADR 0055 D5).

    ``create_schema`` / ``update_schema`` are Pydantic models with
    ``extra="forbid"`` — the generic ``/catalogs/{type}`` routes validate the
    raw body against them (unknown / system-owned fields → 422) before calling
    the adapter, and the domain routes declare the very same models as their
    request bodies. One schema pair per type, shared by both entry points.
    ``supports_promote=False`` marks catalogs with no scope-tier transition
    (the curated taxonomy) — the generic promote route 405s on those.
    """

    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    supports_promote: bool = True


@dataclass(frozen=True)
class CatalogDescriptor:
    type: str
    model: type
    service: CatalogServiceProtocol
    search_columns: tuple[str, ...]
    edge_endpoint_type: EdgeEndpointType
    rbac: CatalogAccessPolicy
    ui: CatalogUiMeta
    concept_link: ConceptLink | None = None
    resolver: Callable[..., Any] | None = None
    write: CatalogWriteContract | None = None
    search_fts_columns: tuple[str, ...] = ()
    search_trigram_columns: tuple[str, ...] = ()
    search_json_columns: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_concept_link(self) -> bool:
        return self.concept_link is not None

    @property
    def fts_columns(self) -> tuple[str, ...]:
        """The full-text surface: the declared override or the listing columns."""
        return self.search_fts_columns or self.search_columns

    @property
    def search_surface(self):
        """This catalog's :class:`~app.core.catalog_search_index.CatalogSearchSurface`."""
        from app.core.catalog_search_index import CatalogSearchSurface

        return CatalogSearchSurface(
            catalog_type=self.type,
            table=self.model.__tablename__,
            fts_columns=self.fts_columns,
            trigram_columns=self.search_trigram_columns,
            json_columns=self.search_json_columns,
        )
