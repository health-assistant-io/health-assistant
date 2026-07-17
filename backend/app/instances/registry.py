"""Registry of per-entity instance search functions.

Each entity module defines an ``async def search(db, tenant_id, patient_id, q,
limit) -> list[dict]`` and registers it via :func:`register_instance_search`.
The dispatcher (:func:`app.services.instance_search_service.search_instances`)
resolves the requested types through this registry.

A runtime registry (rather than a static switch) keeps the set open for
extension — adding an entity type = one module + one ``register_instance_search``
call, no edit to this file. Mirrors the catalog side's registry approach.

The ``type`` string keys here match the frontend ``InstanceType`` union
(``examination | medication | observation | document | event | allergy |
vaccine``).
"""
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

# A search function: (db, tenant_id, optional patient_id, query, per-type cap)
# -> list of hit dicts (matching InstanceSearchHit). ``patient_id`` is None for
# tenant-wide (admin) searches; the function must apply it only when not None.
InstanceSearchFn = Callable[
    [AsyncSession, UUID, Optional[UUID], str, int],
    Awaitable[list[dict]],
]

_REGISTRY: dict[str, InstanceSearchFn] = {}


def register_instance_search(entity_type: str, fn: InstanceSearchFn) -> None:
    """Register a search function for an entity type. Re-registering replaces."""
    _REGISTRY[entity_type] = fn


def get_instance_search(entity_type: str) -> InstanceSearchFn:
    """Resolve a registered search function. Raises KeyError if unregistered."""
    try:
        return _REGISTRY[entity_type]
    except KeyError:
        raise KeyError(
            f"No instance search function registered for type '{entity_type}'."
        ) from None


def registered_types() -> list[str]:
    """All entity types with a registered search function."""
    return list(_REGISTRY.keys())


def _clear_for_tests() -> None:
    """Test-only: clear the registry."""
    _REGISTRY.clear()
