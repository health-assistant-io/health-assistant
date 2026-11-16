"""Own session families — the device list surface (identity-auth §12).

``GET /api/v1/me/sessions`` lists the caller's live ``auth_sessions``
rows (device hint from ``client_label``, §5); ``DELETE
/api/v1/me/sessions/{id}`` revokes one device's family: its refresh
chain dies at once (the row is the authoritative record), and when the
revoked family is the caller's own, the bearer credential is deleted
from the Redis hot layer too (same as ``POST /auth/logout``).
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import token_store
from app.core.authz.policy import RouteClass, policy
from app.core.security import get_current_user, get_token, verify_access_token
from app.schemas.user import TokenData
from app.services.auth.auth_session_service import (
    get_family,
    list_for_user,
    revoke_family,
    revoke_others_for_user,
)

router = APIRouter(prefix="/me", tags=["sessions"])


@router.get("/sessions")
async def list_my_sessions(current_user: TokenData = Depends(get_current_user)):
    """Every live sign-in family for the caller (own devices).

    Each row carries the device label, ``ip``, ``user_agent``,
    ``last_seen_at`` (throttled activity stamp), creation and expiry — the
    facts a user needs to recognise a device, plus ``current`` for the
    family serving this request.
    """
    rows = await list_for_user(current_user.user_id)
    current_fid = current_user.fid
    return [{**row.to_dict(), "current": str(row.id) == str(current_fid)} for row in rows]


@policy(RouteClass.SESSION)
@router.post("/sessions/revoke-others")
async def revoke_other_sessions(current_user: TokenData = Depends(get_current_user)):
    """Sign out every device **except** this one (program 12C1).

    Unlike ``POST /auth/logout-all`` this keeps the current family live and
    does not bump ``users.token_version``, so a user who suspects a lost
    laptop does not have to sign in again on the device they are holding.
    Each revoked family's refresh chain dies immediately (its bound access
    jti leaves the hot layer too) and the sweep is audited
    (``session.revoke_others``).
    """
    revoked = await revoke_others_for_user(
        current_user.user_id,
        keep_family_id=current_user.fid,
        tenant_id=current_user.tenant_id,
    )
    return {"revoked": revoked}


@router.delete("/sessions/{session_id}")
async def revoke_my_session(
    session_id: str,
    current_user: TokenData = Depends(get_current_user),
    token: str = Depends(get_token),
):
    """Revoke one device's family — hidden-404 for anything not owned."""
    family = await get_family(session_id)
    if family is None or str(family.user_id) != str(current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await revoke_family(family.id)
    if str(family.id) == str(current_user.fid):
        # Revoking the device we are calling from: kill the bearer too.
        payload = verify_access_token(token)
        if payload and payload.get("jti"):
            await token_store.revoke_session(str(payload["user_id"]), str(payload["jti"]))
    return {"revoked": True}
