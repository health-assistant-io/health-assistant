from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, Dict, Any, List
from app.core.security import get_current_user, RoleChecker, get_password_hash
from app.services.user_service import get_user_by_id, update_user, delete_user, create_user, get_user_by_email
from app.models.user_model import UserModel
from app.models.enums import Role
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db

from app.schemas.user import TokenData, UserResponse, UserCreate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=List[UserResponse])
async def list_tenant_users(
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),
    db: AsyncSession = Depends(get_db),
):
    """List all users in the current tenant"""
    # System admins see everyone? Or just their own tenant? 
    # Let's start with tenant isolation even for admins.
    query = select(UserModel).where(UserModel.tenant_id == current_user.tenant_id)
    
    # If they are SYSTEM_ADMIN, maybe they want to see everyone? 
    # For now, let's keep it per-tenant as requested for "Tenant Admins".
    
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=UserResponse)
async def create_user_endpoint(
    user_in: UserCreate,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),
):
    """Create a new user within a tenant"""
    # Verify email doesn't exist
    existing_user = await get_user_by_email(user_in.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Enforce tenant isolation
    tenant_id = user_in.tenant_id
    if current_user.role != Role.SYSTEM_ADMIN.value or tenant_id is None:
        # Non-system admins or system admins not providing a tenant_id 
        # default to the current user's tenant.
        tenant_id = current_user.tenant_id

    hashed_password = get_password_hash(user_in.password)
    
    new_user = await create_user(
        email=user_in.email,
        hashed_password=hashed_password,
        tenant_id=tenant_id,
        role=user_in.role
    )
    
    return new_user


@router.get("/me", response_model=UserResponse)
async def get_current_user_endpoint(
    current_user: TokenData = Depends(get_current_user),
):
    """Get current user information"""
    user = await get_user_by_id(current_user.user_id, tenant_id=current_user.tenant_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found in database")

    return user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_endpoint(
    user_id: str, 
    current_user: TokenData = Depends(get_current_user)
):
    """Get user information"""
    # Check permissions: Either you are the user, or you are an admin/manager in the same tenant
    # or you are a SYSTEM_ADMIN.
    
    is_self = str(current_user.user_id) == user_id
    is_admin = current_user.role in [Role.ADMIN.value, Role.MANAGER.value, Role.SYSTEM_ADMIN.value]
    
    # Enforce tenant isolation for non-system admins
    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id
    
    user = await get_user_by_id(user_id, tenant_id=tenant_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Not authorized to view this user")

    return user


@router.put("/{user_id}", response_model=UserResponse)
async def update_user_endpoint(
    user_id: str,
    email: Optional[str] = None,
    role: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
    current_user: TokenData = Depends(get_current_user),
):
    """Update user information"""
    is_self = str(current_user.user_id) == user_id
    is_admin = current_user.role in [Role.ADMIN.value, Role.MANAGER.value, Role.SYSTEM_ADMIN.value]

    if not is_self and not is_admin:
        raise HTTPException(
            status_code=403, detail="Not authorized to update this user"
        )
    
    # Enforce tenant isolation for non-system admins
    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id

    # If updating role, must be admin
    if role and not is_admin:
        raise HTTPException(status_code=403, detail="Only admins can change roles")

    user = await update_user(user_id, email, role, settings, tenant_id=tenant_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.delete("/{user_id}")
async def delete_user_endpoint(
    user_id: str, 
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN]))
):
    """Delete user"""
    # Enforce tenant isolation for non-system admins
    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id
    
    success = await delete_user(user_id, tenant_id=tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted successfully"}
