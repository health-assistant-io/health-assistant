from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import get_current_user
from app.services.alert_service import create_alert, get_alert, list_alerts, trigger_alert, update_alert, delete_alert, get_alert_history

router = APIRouter(prefix="/alerts", tags=["alerts"])

@router.get("/{alert_id}")
async def get_alert_endpoint(alert_id: str, current_user = Depends(get_current_user)):
    """Get alert by ID"""
    alert = await get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    return alert

@router.get("")
async def list_alerts_endpoint(
    patient_id: str = Query(None),
    type: str = Query(None),
    status: str = Query(None),
    current_user = Depends(get_current_user),
    limit: int = Query(50, le=100),
    offset: int = Query(0)
):
    """List alerts (with filtering and pagination)"""
    alerts = await list_alerts(
        tenant_id=current_user.tenant_id,
        patient_id=patient_id,
        alert_type=type,
        limit=limit,
        offset=offset
    )
    return alerts

@router.post("")
async def create_alert_endpoint(
    alert_type: str,
    patient_id: str,
    threshold: float = None,
    enabled: bool = True,
    current_user = Depends(get_current_user)
):
    """Create a new alert"""
    alert = await create_alert(alert_type, patient_id, threshold, enabled, current_user.tenant_id)
    return alert

@router.put("/{alert_id}")
async def update_alert_endpoint(
    alert_id: str,
    threshold: float = None,
    enabled: bool = None,
    current_user = Depends(get_current_user)
):
    """Update alert configuration"""
    alert = await update_alert(alert_id, threshold, enabled)
    return alert

@router.delete("/{alert_id}")
async def delete_alert_endpoint(alert_id: str, current_user = Depends(get_current_user)):
    """Delete an alert"""
    await delete_alert(alert_id)
    return {"message": "Alert deleted successfully"}

@router.post("/{alert_id}/trigger")
async def trigger_alert_endpoint(alert_id: str, current_user = Depends(get_current_user)):
    """Manually trigger an alert"""
    alert = await trigger_alert(alert_id)
    return alert

@router.get("/history")
async def get_alert_history_endpoint(
    patient_id: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    current_user = Depends(get_current_user)
):
    """Get alert history"""
    history = await get_alert_history(patient_id, start_date, end_date)
    return history