from typing import Optional
from uuid import UUID
import logging
from sqlalchemy import select, update, delete
from app.models.user_model import UserModel, Role
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE

logger = logging.getLogger(__name__)


async def get_user_by_email(email: str) -> Optional[UserModel]:
    """Get user by email"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for get_user_by_email")
        return None
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.email == email)
        )
        return result.scalar_one_or_none()


async def get_user_by_id(user_id: str | UUID, tenant_id: Optional[UUID] = None) -> Optional[UserModel]:
    """Get user by ID, optionally filtered by tenant"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for get_user_by_id")
        return None
    
    if isinstance(user_id, str):
        try:
            user_id = UUID(user_id)
        except ValueError:
            logger.error(f"Invalid UUID format: {user_id}")
            return None

    async with AsyncSessionLocal() as session:
        query = select(UserModel).where(UserModel.id == user_id)
        if tenant_id:
            query = query.where(UserModel.tenant_id == tenant_id)
        
        result = await session.execute(query)
        return result.scalar_one_or_none()


async def create_user(email: str, hashed_password: str, tenant_id: str | UUID, role: str = "user") -> UserModel:
    """Create a new user"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for create_user")
        # Return a mock object if DB not available (to avoid breaking things completely)
        return UserModel(
            email=email,
            hashed_password=hashed_password,
            tenant_id=str(tenant_id),
            role=Role(role) if role in [r.value for r in Role] else Role.USER
        )
    
    # Map string role to Enum
    try:
        user_role = Role(role)
    except ValueError:
        user_role = Role.USER

    new_user = UserModel(
        email=email,
        hashed_password=hashed_password,
        tenant_id=str(tenant_id),
        role=user_role,
        settings={}
    )
    
    async with AsyncSessionLocal() as session:
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
    
    return new_user


async def update_user(
    user_id: str | UUID, 
    email: Optional[str] = None, 
    role: Optional[str] = None, 
    settings: Optional[dict] = None,
    tenant_id: Optional[UUID] = None
) -> Optional[UserModel]:
    """Update user information, optionally filtered by tenant"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for update_user")
        return None

    if isinstance(user_id, str):
        try:
            user_id = UUID(user_id)
        except ValueError:
            return None

    update_data = {}
    if email:
        update_data["email"] = email
    if role:
        try:
            update_data["role"] = Role(role)
        except ValueError:
            pass
    if settings is not None:
        update_data["settings"] = settings

    if not update_data:
        return await get_user_by_id(user_id, tenant_id=tenant_id)

    async with AsyncSessionLocal() as session:
        stmt = update(UserModel).where(UserModel.id == user_id)
        if tenant_id:
            stmt = stmt.where(UserModel.tenant_id == tenant_id)
            
        await session.execute(stmt.values(**update_data))
        await session.commit()
        
    return await get_user_by_id(user_id, tenant_id=tenant_id)


async def delete_user(user_id: str | UUID, tenant_id: Optional[UUID] = None) -> bool:
    """Delete user, optionally filtered by tenant"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for delete_user")
        return False

    if isinstance(user_id, str):
        try:
            user_id = UUID(user_id)
        except ValueError:
            return False

    async with AsyncSessionLocal() as session:
        stmt = delete(UserModel).where(UserModel.id == user_id)
        if tenant_id:
            stmt = stmt.where(UserModel.tenant_id == tenant_id)
            
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0
