"""Service layer for the FHIR R4 facade.

Holds the CapabilityStatement builder and any cross-resource helpers used by
the facade endpoints. Per-resource CRUD lives in :mod:`app.services.fhir_service`
(reused); this module is only concerned with the R4 conformance surface.
"""

import os
import re
from typing import Any, Dict, List

from app.facade.registry import RESOURCE_REGISTRY, ResourceEntry
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
    """Build a FHIR R4B CapabilityStatement for the facade.

    The statement is built dynamically from the :data:`RESOURCE_REGISTRY` so
    adding a new resource automatically advertises it. Per-resource
    interactions + search params come from the registered entry.

    Args:
        base_url: the absolute base URL of the facade (e.g.
            ``https://host/api/v1/fhir/R4``). Used in ``implementation.url``
            and to compute per-resource paths.

    Returns:
        A FHIR R4B CapabilityStatement dict (validated by ``fhir.resources``
        on the way out — the caller may call ``parse_fhir_resource`` to verify).
    """
    software_version = get_software_version()

    resources: List[Dict[str, Any]] = []
    for entry in RESOURCE_REGISTRY.all():
        params = sorted(RESOURCE_PARAMS.get(entry.resource_type, set()) | {"_id", "_lastUpdated", "_count", "_sort", "_format"})
        interactions = [
            {"code": code}
            for code in sorted(entry.interactions)
        ]
        search_param_block = [
            {
                "name": name,
                "type": "token" if name not in ("date", "_lastUpdated", "onset-date", "birthdate", "authored-on", "recorded", "sent", "received", "effective") else "date",
                "documentation": f"Search by {name}",
            }
            for name in params
        ]
        resources.append(
            {
                "type": entry.resource_type,
                "interaction": interactions,
                "searchParam": search_param_block,
                "versioning": "versioned" if entry.versioned else "no-version",
                "readHistory": entry.versioned,
                "updateCreate": True,
                "conditionalCreate": False,
                "conditionalRead": "not-supported",
                "conditionalUpdate": False,
                "conditionalDelete": "not-supported",
            }
        )

    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": "2026-06-21T00:00:00Z",
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
                "documentation": "Health Assistant FHIR R4 facade. Tenant-scoped; SMART-on-FHIR scopes deferred to Stage 4.",
                "security": {
                    "cors": True,
                    "description": "Bearer JWT auth via the existing app token (SMART scopes deferred).",
                },
                "resource": resources,
                "interaction": [
                    {"code": "search-system"},
                    {"code": "history-system"},
                    {"code": "batch"},
                    {"code": "transaction"},
                ],
                "searchParam": [
                    {
                        "name": "_id",
                        "type": "token",
                        "documentation": "Logical id of the resource",
                    },
                    {
                        "name": "_lastUpdated",
                        "type": "date",
                        "documentation": "When resource was last updated",
                    },
                ],
                "operation": [
                    {
                        "name": "validate",
                        "definition": f"{base_url}/OperationDefinition/-s-validate",
                    }
                ],
            }
        ],
    }
