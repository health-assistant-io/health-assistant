"""SMART-on-FHIR scope enforcement for the FHIR R4 facade.

``require_fhir_scopes(interaction)`` is the per-route dependency that gates a
facade interaction against the api principal's SMART scopes. ``interaction``
is ``"read"`` (covers ``search-type`` + ``read``) or ``"write"`` (covers
``create`` + ``update`` + ``delete``). Patient-compartment narrowing (for
``patient/`` scoped principals) is applied in :mod:`app.facade.crud` via the
principal's ``bound_patient_id``.

See ``docs/FHIR_R4_FACADE.md`` (Authentication & SMART scopes).
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.core.scopes import scope_allows
from app.core.security import get_api_principal
from app.schemas.user import TokenData


def _forbidden_outcome(resource_type: str, interaction: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "resourceType": "OperationOutcome",
            "issue": [
                {
                    "severity": "error",
                    "code": "forbidden",
                    "diagnostics": (
                        f"Client lacks a SMART scope permitting {interaction} on "
                        f"{resource_type}."
                    ),
                }
            ],
        },
    )


def require_fhir_scopes(interaction: str):
    """Build a dependency that checks the principal may perform ``interaction``
    on the request's ``resource_type`` (resolved from the path by FastAPI)."""

    async def _dep(
        resource_type: str,
        principal: TokenData = Depends(get_api_principal),
    ) -> TokenData:
        if not scope_allows(principal.scope_set, resource_type, interaction):
            raise _forbidden_outcome(resource_type, interaction)
        return principal

    return _dep
