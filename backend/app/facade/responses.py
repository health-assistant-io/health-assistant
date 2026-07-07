"""OperationOutcome + facade response helpers.

The FHIR R4 spec requires that error responses be ``OperationOutcome`` resources
(https://hl7.org/fhir/R4/operationoutcome.html). This module provides builders
that return ``JSONResponse`` with the correct status code + OperationOutcome body,
plus a small helper for success responses with FHIR-canonical headers.
"""

from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse


def operation_outcome(
    severity: str,
    code: str,
    diagnostics: str,
    status_code: int,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    """Build an OperationOutcome JSONResponse.

    ``severity`` ∈ {fatal, error, warning, information}.
    ``code`` is one of the OperationOutcome issue-type codes (invalid, required,
    not-found, exceptional, timeout, etc.). ``diagnostics`` is a human-readable
    explanation.
    """
    issue: Dict[str, Any] = {
        "severity": severity,
        "code": code,
        "diagnostics": diagnostics,
    }
    if extra:
        issue.update(extra)
    return JSONResponse(
        status_code=status_code,
        content={
            "resourceType": "OperationOutcome",
            "issue": [issue],
        },
    )


def not_found(resource_type: str, resource_id: str) -> JSONResponse:
    return operation_outcome(
        severity="error",
        code="not-found",
        diagnostics=f"{resource_type}/{resource_id} not found",
        status_code=404,
    )


def gone(resource_type: str, resource_id: str) -> JSONResponse:
    """410 Gone — resource was deleted (tombstone semantics)."""
    return operation_outcome(
        severity="error",
        code="deleted",
        diagnostics=f"{resource_type}/{resource_id} was deleted",
        status_code=410,
    )


def invalid(diagnostics: str) -> JSONResponse:
    return operation_outcome(
        severity="error",
        code="invalid",
        diagnostics=diagnostics,
        status_code=400,
    )


def created_response(
    resource: Dict[str, Any],
    location: str,
    etag: str,
    last_modified: Optional[str] = None,
) -> JSONResponse:
    """201 Created with Location, ETag, Last-Modified headers + canonical body."""
    headers: Dict[str, str] = {
        "Location": location,
        "ETag": etag,
    }
    if last_modified:
        headers["Last-Modified"] = last_modified
    return JSONResponse(status_code=201, content=resource, headers=headers)


def ok_response(
    resource: Dict[str, Any], etag: str, last_modified: Optional[str] = None
) -> JSONResponse:
    """200 OK with ETag + canonical body."""
    headers: Dict[str, str] = {"ETag": etag}
    if last_modified:
        headers["Last-Modified"] = last_modified
    return JSONResponse(status_code=200, content=resource, headers=headers)


def no_content() -> JSONResponse:
    """204 No Content (successful delete)."""
    return JSONResponse(status_code=204, content=None)
