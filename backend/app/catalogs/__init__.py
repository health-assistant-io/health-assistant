"""Catalog Registry package — the uniform contract over all clinical catalogs.

Importing this package (``from app.catalogs import CatalogRegistry``) populates
the registry with every registered catalog via
:mod:`app.catalogs.registrations`. Consumers never hardcode the catalog list.

See ``dev/plans/unified-catalog-architecture-2026-07-08.md``.
"""

from app.catalogs.descriptors import CatalogDescriptor
from app.catalogs.policy import (
    DEFAULT_CATALOG_POLICY,
    CatalogAccessPolicy,
    CatalogPermissionDenied,
)
from app.catalogs.protocol import (
    CatalogServiceProtocol,
    CatalogUiMeta,
    ConceptLink,
)
from app.catalogs.registry import CatalogRegistry
from app.catalogs import registrations  # noqa: F401 — populates the registry

__all__ = [
    "CatalogAccessPolicy",
    "CatalogDescriptor",
    "CatalogPermissionDenied",
    "CatalogRegistry",
    "CatalogServiceProtocol",
    "CatalogUiMeta",
    "ConceptLink",
    "DEFAULT_CATALOG_POLICY",
]
