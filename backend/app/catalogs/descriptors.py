"""The :class:`CatalogDescriptor` — one immutable entry per catalog type.

A descriptor bundles everything the meta-layer, search dispatcher, graph
service, and FHIR facade need to treat a catalog uniformly:

- ``model`` — the SQLAlchemy ORM class (single source of truth for storage).
- ``service`` — a :class:`~app.catalogs.protocol.CatalogServiceProtocol` adapter.
- ``search_columns`` — text columns the trigram/FTS search runs over.
- ``concept_link`` — the taxonomy FK (``class_concept_id`` convention).
- ``edge_endpoint_type`` — how this catalog participates in ``concept_edges``.
- ``resolver`` — bulk endpoint resolver for graph display payloads.
- ``fhir_projector`` — ``to_fhir_dict`` projection (optional).
- ``rbac`` — the uniform access policy.
- ``ui`` — frontend metadata for the admin workspace.

Adding a catalog = one :meth:`CatalogRegistry.register` call (plus, for a new
table, one model + one resolver). Every consumer of the registry then gains the
new catalog automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.catalogs.policy import CatalogAccessPolicy
from app.catalogs.protocol import CatalogServiceProtocol, CatalogUiMeta, ConceptLink
from app.models.enums import EdgeEndpointType


@dataclass(frozen=True)
class CatalogDescriptor:
    type: str
    model: type
    service: CatalogServiceProtocol
    search_columns: tuple[str, ...]
    edge_endpoint_type: EdgeEndpointType
    rbac: CatalogAccessPolicy
    ui: CatalogUiMeta
    concept_link: Optional[ConceptLink] = None
    resolver: Optional[Callable[..., Any]] = None
    fhir_projector: Optional[Callable[[Any], Any]] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_concept_link(self) -> bool:
        return self.concept_link is not None
