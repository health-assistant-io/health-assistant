"""Process-global registry of all clinical catalogs.

Populated at import time by :mod:`app.catalogs.registrations` (imported once
via :mod:`app.catalogs.__init__`). Consumers read from this registry and never
hardcode the list of catalogs — that is what keeps the meta-layer, search
dispatcher, graph service, and LLM tools closed for modification.
"""

from __future__ import annotations

from app.catalogs.descriptors import CatalogDescriptor


class CatalogRegistry:
    _by_type: dict[str, CatalogDescriptor] = {}

    @classmethod
    def register(cls, descriptor: CatalogDescriptor) -> None:
        if descriptor.type in cls._by_type:
            raise ValueError(f"Catalog type '{descriptor.type}' is already registered")
        cls._by_type[descriptor.type] = descriptor

    @classmethod
    def get(cls, type: str) -> CatalogDescriptor:
        try:
            return cls._by_type[type]
        except KeyError:
            raise KeyError(f"Unknown catalog type '{type}'") from None

    @classmethod
    def types(cls) -> list[str]:
        return list(cls._by_type.keys())

    @classmethod
    def all(cls) -> list[CatalogDescriptor]:
        return list(cls._by_type.values())

    @classmethod
    def is_registered(cls, type: str) -> bool:
        return type in cls._by_type

    @classmethod
    def _reset(cls) -> None:
        """Test-only: clear registrations so a test can re-import cleanly."""
        cls._by_type = {}
