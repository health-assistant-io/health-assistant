"""Regression tests for audit C1 — domain exception hierarchy + handler.

Covers:
1. The hierarchy: each subclass carries the right HTTP ``status_code``;
   ``ConcurrencyError`` inherits from ``ConflictError``.
2. The ``main.domain_error_handler`` maps each to its status with a safe detail.
3. The access-check helpers (``check_*_access``) now raise domain exceptions
   (NotFoundError/AuthorizationError/ValidationError) instead of HTTPException —
   and those surface through the handler as the same 404/403/400 the endpoints
   always returned.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.access import check_patient_access
from app.core.database import AsyncSessionLocal
from app.core.errors import (
    AuthorizationError,
    ConcurrencyError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.main import domain_error_handler
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.schemas.user import TokenData


def test_hierarchy_status_codes():
    assert DomainError().status_code == 500
    assert NotFoundError().status_code == 404
    assert AuthorizationError().status_code == 403
    assert ValidationError().status_code == 400
    assert ConflictError().status_code == 409
    assert ConcurrencyError().status_code == 409  # inherits Conflict
    assert issubclass(ConcurrencyError, ConflictError)
    # detail defaults to the class name when not given
    assert NotFoundError().detail == "NotFoundError"
    assert NotFoundError("Patient not found").detail == "Patient not found"


def _app_with_routes():
    app = FastAPI()

    @app.exception_handler(DomainError)
    async def _h(request, exc):  # noqa: ANN001
        return await domain_error_handler(request, exc)

    @app.get("/nf")
    def _nf():
        raise NotFoundError("Patient not found")

    @app.get("/auth")
    def _auth():
        raise AuthorizationError("nope")

    @app.get("/val")
    def _val():
        raise ValidationError("bad input")

    @app.get("/conflict")
    def _conflict():
        raise ConflictError("exists")

    return app


def test_handler_maps_domain_errors_to_http():
    client = TestClient(_app_with_routes(), raise_server_exceptions=False)
    assert client.get("/nf").status_code == 404
    assert client.get("/nf").json()["detail"] == "Patient not found"
    assert client.get("/auth").status_code == 403
    assert client.get("/val").status_code == 400
    assert client.get("/conflict").status_code == 409


@pytest.mark.asyncio
async def test_check_patient_access_raises_domain_errors():
    """The access helpers raise domain exceptions (not HTTPException); the
    message contract is unchanged so endpoints/tests asserting on detail still
    pass."""
    tenant = TenantModel(id=uuid.uuid4(), name="C1", slug=f"c1-{uuid.uuid4().hex[:8]}")
    async with AsyncSessionLocal() as session:
        session.add(tenant)
        await session.commit()

    current_user = TokenData(
        user_id=uuid.uuid4(),
        tenant_id=tenant.id,
        role="USER",
        sub="user@test.local",
    )

    # Invalid UUID -> ValidationError
    with pytest.raises(ValidationError):
        async with AsyncSessionLocal() as session:
            await check_patient_access("not-a-uuid", current_user, session)

    # Non-existent patient -> NotFoundError
    with pytest.raises(NotFoundError):
        async with AsyncSessionLocal() as session:
            await check_patient_access(uuid.uuid4(), current_user, session)

    # Existing patient with no linked user; a USER-role caller is denied
    # (the ``not patient.user_id`` branch) -> AuthorizationError.
    other_patient = Patient(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name={"family": "Other"},
        gender="UNKNOWN",
        user_id=None,
    )
    async with AsyncSessionLocal() as session:
        session.add(other_patient)
        await session.commit()
        with pytest.raises(AuthorizationError):
            await check_patient_access(other_patient.id, current_user, session)
        # cleanup
        await session.delete(other_patient)
        await session.commit()

    async with AsyncSessionLocal() as session:
        await session.delete(tenant)
        await session.commit()
