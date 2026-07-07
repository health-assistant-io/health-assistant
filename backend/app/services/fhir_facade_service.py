"""Service layer for the FHIR R4 facade.

Holds the CapabilityStatement builder and any cross-resource helpers used by
the facade endpoints. Per-resource CRUD lives in :mod:`app.services.fhir_service`
(reused); this module is only concerned with the R4 conformance surface.
"""

import datetime as _dt
import os
import re
from typing import Any, Dict, List

from app.facade.registry import RESOURCE_REGISTRY
from app.facade.search_params import RESOURCE_PARAMS
from app.schemas.backup import FHIR_VERSION


def get_software_version() -> str:
    """Read the project version from the same source as ``version_manager``.

    The version is stored as ``VERSION: str = "X.Y.Z[-suffix]"`` in
    :mod:`app.core.config`. Falls back to ``"0.0.0+unknown"`` if unreadable.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(here, "..", "core", "config.py")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        match = re.search(r'VERSION:\s*str\s*=\s*"([^"]+)"', text)
        if match:
            return match.group(1)
    except OSError:
        pass
    return "0.0.0+unknown"


def build_capability_statement(base_url: str) -> Dict[str, Any]:
    """Build a FHIR R4 CapabilityStatement for the facade.

    The statement is built dynamically from the :data:`RESOURCE_REGISTRY` so
    adding a new resource automatically advertises it. Per-resource
    interactions + search params come from the registered entry.

    Advertises ``fhirVersion = "4.0.1"`` (R4) to match the ``/fhir/R4/`` path.
    Validation of the resources we emit is done by ``fhir.resources`` (R4B
    subpackage — see ``app.schemas.backup.FHIR_VERSION`` for rationale); R4B
    is a superset of R4 for every field our models project.

    Args:
        base_url: the absolute base URL of the facade (e.g.
            ``https://host/api/v1/fhir/R4``). Used in ``implementation.url``
            and to compute per-resource paths.

    Returns:
        A FHIR R4 CapabilityStatement dict (validated by ``fhir.resources``
        on the way out — the caller may call ``parse_fhir_resource`` to verify).
    """
    software_version = get_software_version()

    resources: List[Dict[str, Any]] = []
    for entry in RESOURCE_REGISTRY.all():
        params = sorted(
            RESOURCE_PARAMS.get(entry.resource_type, set())
            | {"_id", "_lastUpdated", "_count", "_sort", "_format"}
        )
        interactions = [{"code": code} for code in sorted(entry.interactions)]
        search_param_block = [
            {
                "name": name,
                "type": "token"
                if name
                not in (
                    "date",
                    "_lastUpdated",
                    "onset-date",
                    "birthdate",
                    "authored-on",
                    "recorded",
                    "sent",
                    "received",
                    "effective",
                )
                else "date",
                "documentation": f"Search by {name}",
            }
            for name in params
        ]
        resources.append(
            {
                "type": entry.resource_type,
                "interaction": interactions,
                "searchParam": search_param_block,
                # Honest versioning: VersionedMixin bumps `version` in place but
                # we don't implement vread / history-instance / version history
                # yet. Declare no-version so clients don't try to dereference
                # the versionId we put in the ETag/Location. When vread lands,
                # flip back to "versioned".
                "versioning": "no-version",
                "readHistory": False,
                # PUT to a missing id returns 404 (not update-as-create), so
                # advertise that honestly. Toggle to True if/when implemented.
                "updateCreate": False,
                "conditionalCreate": False,
                "conditionalRead": "not-supported",
                "conditionalUpdate": False,
                "conditionalDelete": "not-supported",
            }
        )

    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "publisher": "Health Assistant",
        "kind": "instance",
        "fhirVersion": FHIR_VERSION,
        "format": ["json", "application/fhir+json"],
        "patchFormat": [],
        "implementationGuide": [],
        "software": {
            "name": "Health Assistant",
            "version": software_version,
        },
        "implementation": {
            "description": "Health Assistant FHIR R4 Facade",
            "url": base_url,
        },
        "rest": [
            {
                "mode": "server",
                "documentation": "Health Assistant FHIR R4 facade. Tenant-scoped. Two auth modes: (1) platform user JWT (login), (2) service-account JWT (admin-minted, long-lived, for external systems — POST /auth/service-account). SYSTEM_ADMIN can override tenant scope via the X-Tenant header. SMART-on-FHIR (OAuth2 + scopes) is deferred to Stage 4.",
                "security": {
                    "cors": True,
                    "description": "Bearer JWT auth. Two modes: (1) platform user JWT via /auth/login; (2) service-account JWT via /auth/service-account (for external systems). SMART-on-FHIR deferred to Stage 4.",
                },
                "resource": resources,
                # System-wide interactions: only advertise what the root /fhir/R4
                # endpoint actually supports. POST to /fhir/R4 is treated as a
                # 404/405 (no batch/transaction dispatch), there is no
                # /fhir/R4/_history (history-system), and /fhir/R4/_search is not
                # implemented (search-system). Leave the list empty rather than
                # falsely advertising batch/transaction/history-system/search-system.
                "interaction": [],
                # System-wide search params (_id, _lastUpdated) are advertised
                # per-resource above; no need to duplicate at the system level.
                "searchParam": [],
                # /$validate operation (POST /fhir/R4/$validate) is not
                # implemented. Don't advertise it until it lands.
                "operation": [],
            }
        ],
    }
