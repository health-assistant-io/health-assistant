"""OAuth2 client-credentials token endpoint + client management.

The FHIR R4 facade (``/api/v1/fhir/R4/*``) is Health Assistant's public
interop surface. External systems authenticate with the OAuth2
**client-credentials** grant (RFC 6749 §4.4) and receive a short-lived JWT
carrying SMART-on-FHIR scopes.

* ``POST /oauth/token``     — RFC 6749 §4.4 grant (form or JSON; HTTP Basic
  or body client credentials). Returns ``{access_token, token_type, expires_in,
  scope, tenant_id}``.
* ``POST /oauth/revoke``    — RFC 7009 best-effort revocation (jti blocklist).
* ``GET  /oauth/clients``   — list clients in the caller's tenant (admin).
* ``POST /oauth/clients``   — register a client; returns the plaintext secret
  **once**.
* ``POST /oauth/clients/{id}/rotate-secret`` — new plaintext secret.
* ``PATCH /oauth/clients/{id}`` / ``DELETE`` — edit / revoke.

See ``docs/API_LAYERS.md`` and ``docs/FHIR_R4_FACADE.md``.
"""
import base64
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.scopes import (
    InvalidScopeError,
    has_patient_context,
    intersect_scopes,
    parse_scopes,
    validate_registrable_scopes,
)
from app.core.security import (
    RoleChecker,
    create_api_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.core import token_store
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.models.oauth import OAuthClient
from app.schemas.oauth import (
    OAuthClientCreate,
    OAuthClientCreateResponse,
    OAuthClientResponse,
    OAuthClientUpdate,
)
from app.schemas.user import TokenData

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _new_client_id() -> str:
    return "ci_" + secrets.token_urlsafe(18)


def _new_client_secret() -> str:
    return secrets.token_urlsafe(40)


def _resolve_target_tenant(current_user: TokenData, requested: Optional[str]) -> str:
    """Return the tenant a client operation targets, enforcing RBAC.

    Non-SYSTEM_ADMIN callers can only operate on their own tenant.
    """
    target = requested or str(current_user.tenant_id)
    if str(current_user.tenant_id) != target and current_user.role != Role.SYSTEM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot manage OAuth clients in a different tenant.",
        )
    return target


async def _load_client_for_owner(
    db: AsyncSession, client_row_id: str, current_user: TokenData
) -> OAuthClient:
    """Load a client row scoped to the caller's tenant (SYSTEM_ADMIN = any)."""
    from uuid import UUID

    try:
        row_id = UUID(str(client_row_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Client not found")

    stmt = select(OAuthClient).where(OAuthClient.id == row_id)
    if current_user.role != Role.SYSTEM_ADMIN.value:
        stmt = stmt.where(OAuthClient.tenant_id == current_user.tenant_id)
    result = await db.execute(stmt)
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


async def _validate_bound_patient(
    db: AsyncSession, tenant_id: str, patient_id
) -> None:
    """Ensure the bound patient exists in the tenant (patient/ scopes)."""
    result = await db.execute(
        select(Patient.id).where(
            Patient.id == patient_id, Patient.tenant_id == tenant_id
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bound_patient_id does not reference a patient in this tenant.",
        )


# ---------------------------------------------------------------------------
# Token endpoint (RFC 6749 §4.4)
# ---------------------------------------------------------------------------


async def _extract_client_credentials(
    request: Request, data: dict
) -> tuple[str, str]:
    """Client id+secret from HTTP Basic (RFC §2.3.1) or the request body."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            cid, _, csec = decoded.partition(":")
            if cid and csec:
                return cid, csec
        except Exception:
            pass
    cid = data.get("client_id")
    csec = data.get("client_secret")
    if not cid or not csec:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client: client_id and client_secret are required "
            "(HTTP Basic or request body).",
            headers={"WWW-Authenticate": "Basic"},
        )
    return cid, csec


@router.post("/token")
async def token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit("oauth_token", max_requests=30, window=60)),
):
    """OAuth2 client-credentials grant (RFC 6749 §4.4).

    Accepts ``application/x-www-form-urlencoded`` (standard) or JSON. Client
    credentials may be supplied via HTTP Basic or the request body. Returns a
    short-lived access token (JWT) carrying SMART-on-FHIR scopes.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    grant_type = (data.get("grant_type") or "").strip()
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported_grant_type: only 'client_credentials' is supported, got '{grant_type}'.",
        )

    client_id, client_secret = await _extract_client_credentials(request, data)

    result = await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    client = result.scalar_one_or_none()
    if (
        client is None
        or not client.is_active
        or not client.is_confidential
        or not client.client_secret_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client: unknown, inactive, or non-confidential client.",
            headers={"WWW-Authenticate": "Basic"},
        )

    if not verify_password(client_secret, client.client_secret_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_client: authentication failed.",
            headers={"WWW-Authenticate": "Basic"},
        )

    registered = list(client.scopes) if client.scopes else []
    requested = parse_scopes(data.get("scope"))
    if requested:
        # Stricter posture (RFC §3.3): any requested scope not granted at
        # registration is rejected rather than silently dropped.
        ungranted = requested - set(registered)
        if ungranted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid_scope: requested scopes not granted to this client: "
                + ", ".join(sorted(ungranted)),
            )
    granted = intersect_scopes(requested, registered)

    expires_delta = timedelta(minutes=settings.OAUTH_ACCESS_TOKEN_TTL_MINUTES)
    access_token, _jti = create_api_access_token(
        client_id=client.client_id,
        tenant_id=str(client.tenant_id),
        scopes=granted,
        bound_patient_id=str(client.bound_patient_id) if client.bound_patient_id else None,
        expires_delta=expires_delta,
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": int(expires_delta.total_seconds()),
        "scope": " ".join(granted),
        "tenant_id": str(client.tenant_id),
    }


@router.post("/revoke")
async def revoke(
    request: Request,
    current_user: TokenData = Depends(get_current_user),
):
    """RFC 7009 token revocation (best-effort for stateless JWTs).

    Records the token's ``jti`` in the revocation blocklist for its remaining
    lifetime. Always returns 200 (per spec, unknown/invalid tokens don't error)
    so the endpoint doesn't leak validity information.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)
    token_value = data.get("token")
    if token_value:
        from app.core.security import decode_access_token

        payload = decode_access_token(token_value)
        jti = payload.get("jti") if payload else None
        exp = payload.get("exp") if payload else None
        if jti and exp:
            import time

            remaining = max(int(exp - time.time()), 1)
            await token_store.revoke_api_jti(jti, remaining)
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Client management (tenant administrators)
# ---------------------------------------------------------------------------


@router.get("/clients", response_model=list[OAuthClientResponse])
async def list_clients(
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),
    db: AsyncSession = Depends(get_db),
):
    """List OAuth clients in the caller's tenant (SYSTEM_ADMIN: all)."""
    stmt = select(OAuthClient)
    if current_user.role != Role.SYSTEM_ADMIN.value:
        stmt = stmt.where(OAuthClient.tenant_id == current_user.tenant_id)
    stmt = stmt.order_by(OAuthClient.created_at.desc())
    result = await db.execute(stmt)
    return [OAuthClientResponse.from_model(c) for c in result.scalars().all()]


@router.post("/clients", response_model=OAuthClientCreateResponse, status_code=201)
async def create_client(
    payload: OAuthClientCreate,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),
    db: AsyncSession = Depends(get_db),
):
    """Register a new OAuth client. Returns the plaintext secret **once**."""
    try:
        scopes = validate_registrable_scopes(payload.scopes)
    except InvalidScopeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    target_tenant = _resolve_target_tenant(current_user, str(payload.tenant_id) if payload.tenant_id else None)

    if has_patient_context(scopes):
        if payload.bound_patient_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A bound_patient_id is required when granting patient/ scopes.",
            )
        await _validate_bound_patient(db, target_tenant, payload.bound_patient_id)
    elif payload.bound_patient_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bound_patient_id is only valid alongside patient/ scopes.",
        )

    plaintext_secret = _new_client_secret()
    client = OAuthClient(
        client_id=_new_client_id(),
        client_secret_hash=get_password_hash(plaintext_secret),
        tenant_id=target_tenant,
        display_name=payload.display_name,
        scopes=scopes,
        bound_patient_id=payload.bound_patient_id,
        is_confidential=True,
        is_active=True,
        created_by_user_id=current_user.user_id,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    base = OAuthClientResponse.from_model(client)
    return OAuthClientCreateResponse(**base.model_dump(), client_secret=plaintext_secret)


@router.post("/clients/{client_row_id}/rotate-secret")
async def rotate_secret(
    client_row_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),
    db: AsyncSession = Depends(get_db),
):
    """Issue a new client secret; the old hash is overwritten."""
    client = await _load_client_for_owner(db, client_row_id, current_user)
    plaintext_secret = _new_client_secret()
    client.client_secret_hash = get_password_hash(plaintext_secret)
    await db.commit()
    return {"client_id": client.client_id, "client_secret": plaintext_secret}


@router.patch("/clients/{client_row_id}", response_model=OAuthClientResponse)
async def update_client(
    client_row_id: str,
    payload: OAuthClientUpdate,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),
    db: AsyncSession = Depends(get_db),
):
    """Update a client's metadata / scopes / active flag.

    Scope changes take effect on the next token mint. Switching to include
    ``patient/`` scopes requires a ``bound_patient_id``; clearing scopes to
    non-patient clears the binding.
    """
    client = await _load_client_for_owner(db, client_row_id, current_user)

    if payload.display_name is not None:
        client.display_name = payload.display_name
    if payload.is_active is not None:
        client.is_active = payload.is_active
    if payload.scopes is not None:
        try:
            scopes = validate_registrable_scopes(payload.scopes)
        except InvalidScopeError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        if has_patient_context(scopes):
            bound = payload.bound_patient_id or client.bound_patient_id
            if bound is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A bound_patient_id is required when granting patient/ scopes.",
                )
            await _validate_bound_patient(db, str(client.tenant_id), bound)
            client.bound_patient_id = bound
        else:
            client.bound_patient_id = None
        client.scopes = scopes
    elif payload.bound_patient_id is not None and has_patient_context(client.scopes):
        await _validate_bound_patient(db, str(client.tenant_id), payload.bound_patient_id)
        client.bound_patient_id = payload.bound_patient_id

    await db.commit()
    await db.refresh(client)
    return OAuthClientResponse.from_model(client)


@router.delete("/clients/{client_row_id}")
async def delete_client(
    client_row_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a client. Outstanding tokens stay valid until they expire
    (rely on the short TTL); rotate or disable first to cut access sooner."""
    client = await _load_client_for_owner(db, client_row_id, current_user)
    await db.delete(client)
    await db.commit()
    return {"message": "Client deleted."}
