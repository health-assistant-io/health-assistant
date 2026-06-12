from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user, RoleChecker
from app.services.tenant_service import get_tenant, create_tenant, update_tenant, delete_tenant
from app.models.enums import Role
from app.schemas.user import TokenData

router = APIRouter(prefix="/tenants", tags=["tenants"])

@router.get("/{tenant_id}")
async def get_tenant_endpoint(tenant_id: str, current_user: TokenData = Depends(get_current_user)):
    """Get tenant information. Only System Admins or the Tenant Admin of this tenant can see this."""
    if current_user.role != Role.SYSTEM_ADMIN.value and str(current_user.tenant_id) != tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this tenant")
        
    tenant = await get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant

@router.post("")
async def create_tenant_endpoint(
    name: str, 
    settings: dict, 
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN]))
):
    """Create a new tenant (organization) - System Admin only"""
    tenant = await create_tenant(name, settings)
    return tenant

@router.put("/{tenant_id}")
async def update_tenant_endpoint(
    tenant_id: str, 
    name: str = None, 
    settings: dict = None, 
    current_user: TokenData = Depends(get_current_user)
):
    """Update tenant information. Only System Admins or Tenant Admins of this tenant."""
    is_system_admin = current_user.role == Role.SYSTEM_ADMIN.value
    is_tenant_admin = current_user.role == Role.ADMIN.value and str(current_user.tenant_id) == tenant_id
    
    if not is_system_admin and not is_tenant_admin:
        raise HTTPException(status_code=403, detail="Not authorized to update this tenant")
    
    tenant = await update_tenant(tenant_id, name, settings)
    return tenant

@router.delete("/{tenant_id}")
async def delete_tenant_endpoint(
    tenant_id: str, 
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN]))
):
    """Delete tenant - System Admin only"""
    await delete_tenant(tenant_id)
    return {"message": "Tenant deleted successfully"}
