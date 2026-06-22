"""Alert endpoints — patient threshold notifications.

Audit item B4: previously every endpoint called ``alert_service`` by bare
``alert_id`` / ``patient_id`` and never re-checked tenancy. Any
authenticated user could read / update / delete / fire any alert in the
system regardless of which tenant it belonged to.

Every endpoint now:
1. Threads ``current_user.tenant_id`` into ``alert_service.*`` so the
   service-layer SELECT/UPDATE/DELETE is constrained to the caller's
   tenant.
2. Calls ``check_patient_access`` first for patient-scoped routes so a
   ``USER``-role caller can only touch patients assigned to them.
3. Treats a cross-tenant call as 404 (no information leak).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.utils import check_patient_access
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.services.alert_service import (
    create_alert,
    delete_alert,
    get_alert,
    get_alert_history,
    list_alerts,
    trigger_alert,
    update_alert,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/{alert_id}")
async def get_alert_endpoint(
    alert_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get alert by ID.

    Tenant-scoped: cross-tenant call returns 404 (no information leak).
    """
    alert = await get_alert(alert_id, tenant_id=current_user.tenant_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.get("")
async def list_alerts_endpoint(
    patient_id: str = Query(None),
    type: str = Query(None),
    status: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
):
    """List alerts (with filtering and pagination).

    Tenant-scoped via ``alert_service.list_alerts``. If ``patient_id`` is
    supplied, ``check_patient_access`` is called first so a USER cannot
    enumerate another user's patients.
    """
    if patient_id:
        await check_patient_access(patient_id, current_user, db)

    alerts = await list_alerts(
        tenant_id=current_user.tenant_id,
        patient_id=patient_id,
        alert_type=type,
        limit=limit,
        offset=offset,
    )
    return alerts


@router.post("")
async def create_alert_endpoint(
    alert_type: str,
    patient_id: str,
    threshold: float = None,
    enabled: bool = True,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new alert.

    Patient-access scoped: a USER caller can only create alerts on
    patients assigned to them.
    """
    await check_patient_access(patient_id, current_user, db)

    alert = await create_alert(
        alert_type,
        patient_id,
        threshold,
        enabled,
        current_user.tenant_id,
    )
    return alert


@router.put("/{alert_id}")
async def update_alert_endpoint(
    alert_id: str,
    threshold: float = None,
    enabled: bool = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update alert configuration.

    Tenant-scoped: cross-tenant call returns 404.
    """
    alert = await update_alert(
        alert_id,
        threshold,
        enabled,
        tenant_id=current_user.tenant_id,
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.delete("/{alert_id}")
async def delete_alert_endpoint(
    alert_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an alert.

    Tenant-scoped: cross-tenant delete is a no-op. We still return
    success to avoid leaking existence (matches the notification pattern).
    """
    await delete_alert(alert_id, tenant_id=current_user.tenant_id)
    return {"message": "Alert deleted successfully"}


@router.post("/{alert_id}/trigger")
async def trigger_alert_endpoint(
    alert_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger an alert.

    Tenant-scoped: cross-tenant call returns 404.
    """
    alert = await trigger_alert(
        alert_id, tenant_id=current_user.tenant_id
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.get("/history")
async def get_alert_history_endpoint(
    patient_id: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get alert history.

    Tenant-scoped. If ``patient_id`` is supplied, ``check_patient_access``
    is called first.
    """
    if patient_id:
        await check_patient_access(patient_id, current_user, db)

    history = await get_alert_history(
        patient_id,
        start_date,
        end_date,
        tenant_id=current_user.tenant_id,
    )
    return history
