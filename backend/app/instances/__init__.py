"""Unified Instance Search — package init.

Patient-scoped clinical records (examinations, medications, observations,
documents, clinical events, allergies, vaccines) each ship a search function
here that runs a tenant- (+ optional patient-) scoped ILIKE query and returns
uniform :class:`~app.schemas.instance_search.InstanceSearchHit` dicts. The
functions self-register in :mod:`app.instances.registry`; the dispatcher in
:mod:`app.services.instance_search_service` fans a query out across the
requested types.

This is the instance-side counterpart of the catalog ``search_catalogs``
dispatcher. Security is enforced centrally in the HTTP endpoint
(``GET /instances/search``): every registered search function receives
``tenant_id`` and (when scoped) ``patient_id`` as explicit required arguments
and MUST filter on both — they never perform access checks themselves, so the
single endpoint chokepoint can't be bypassed per-entity.
"""

# Importing the entity modules registers their search functions with the
# registry (side effect). Done here so any importer of the package (the
# dispatcher service, the endpoint, tests) gets the full set registered. Each
# module imports only sibling/standard modules, so there is no import cycle.
from app.instances import (  # noqa: F401 (side-effect registration)
    examination as _examination,
    medication as _medication,
    observation as _observation,
    document as _document,
    event as _event,
    allergy as _allergy,
    vaccine as _vaccine,
)
