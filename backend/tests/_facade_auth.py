"""Shared helper: mint an OAuth2 api token for FHIR facade tests.

The FHIR R4 facade is api-only (session JWTs are rejected — see
``docs/API_LAYERS.md``). Facade tests therefore need an api token. Minting
directly via ``create_api_access_token`` keeps these tests focused on facade
behavior; the OAuth client-credentials flow itself is covered in
``test_oauth_client_credentials.py``.
"""
import uuid

from app.core.security import create_api_access_token


async def facade_api_headers(tenant_id, *, scopes=None):
    """Return ``{"Authorization": "Bearer <api token>"}`` for the given tenant.

    Defaults to a ``system/*.*`` scope (full tenant-level facade access), which
    is what conformance tests want. Pass ``scopes`` for scope-enforcement tests.
    """
    token, _ = create_api_access_token(
        client_id=f"ci-test-{uuid.uuid4().hex[:12]}",
        tenant_id=str(tenant_id),
        scopes=scopes or ["system/*.*"],
    )
    return {"Authorization": f"Bearer {token}"}
