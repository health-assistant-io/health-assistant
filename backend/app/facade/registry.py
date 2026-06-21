"""Resource registry for the FHIR R4 facade.

Each FHIR resource type exposed by the facade registers a :class:`ResourceEntry`
here. The registry is consumed by:

* :mod:`app.services.fhir_facade_service` — to build the CapabilityStatement
* :mod:`app.api.v1.facade.search` — to dispatch search/read by resource type
* :mod:`app.api.v1.endpoints.fhir_r4` — to wire up the HTTP routes

A resource entry binds together:

* the FHIR resource type name (e.g. ``"Condition"``)
* the ORM model class
* the FHIR→ORM converter function (from :mod:`app.services.fhir_converter`)
* the list of FHIR search params this resource supports
* the list of interactions (read, search-type, create, update, delete)

Adding a new resource to the facade = register here + ensure the model has
``to_fhir_dict()`` + a converter exists in ``fhir_converter``. Typically <50 LOC.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Type


@dataclass
class ResourceEntry:
    """Registration record for one FHIR resource type."""

    resource_type: str
    model: Type[Any]
    to_fhir_dict_attr: str = "to_fhir_dict"
    fhir_to_orm_fn: Optional[Callable] = None
    search_params: FrozenSet[str] = field(default_factory=frozenset)
    interactions: FrozenSet[str] = field(default_factory=lambda: frozenset({"read", "search-type", "create", "update", "delete"}))
    versioned: bool = True
    soft_delete: bool = True
    # The reference path under ``/fhir/R4/`` (e.g. ``/Condition``).
    path: Optional[str] = None

    @property
    def route_path(self) -> str:
        return self.path or f"/{self.resource_type}"


class _ResourceRegistry:
    """Mutable registry, accessed via :data:`RESOURCE_REGISTRY`."""

    def __init__(self) -> None:
        self._entries: Dict[str, ResourceEntry] = {}

    def register(self, entry: ResourceEntry) -> ResourceEntry:
        if entry.resource_type in self._entries:
            raise ValueError(f"{entry.resource_type} already registered")
        self._entries[entry.resource_type] = entry
        return entry

    def get(self, resource_type: str) -> Optional[ResourceEntry]:
        return self._entries.get(resource_type)

    def all(self) -> List[ResourceEntry]:
        # Stable order by resource_type for CapabilityStatement determinism.
        return sorted(self._entries.values(), key=lambda e: e.resource_type)

    def types(self) -> List[str]:
        return sorted(self._entries.keys())

    def __contains__(self, resource_type: str) -> bool:
        return resource_type in self._entries

    def __len__(self) -> int:
        return len(self._entries)


# Singleton. Resources register themselves at import time of
# ``app.api.v1.facade.routes`` (which is imported by the facade router module).
RESOURCE_REGISTRY = _ResourceRegistry()
