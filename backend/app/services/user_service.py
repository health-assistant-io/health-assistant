import logging
from uuid import UUID

from sqlalchemy import delete, select, update

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.models.user_model import Role, UserModel

logger = logging.getLogger(__name__)


async def get_user_by_email(email: str) -> UserModel | None:
    """Get user by email"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for get_user_by_email")
        return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.email == email)
        )
        return result.scalar_one_or_none()


async def get_user_by_id(
    user_id: str | UUID, tenant_id: UUID | None = None
) -> UserModel | None:
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


async def create_user(
    email: str, hashed_password: str, tenant_id: str | UUID, role: str = "user"
) -> UserModel:
    """Create a new user"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for create_user")
        # Return a mock object if DB not available (to avoid breaking things completely)
        return UserModel(
            email=email,
            hashed_password=hashed_password,
            tenant_id=str(tenant_id),
            role=Role(role) if role in [r.value for r in Role] else Role.USER,
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
        settings={},
    )

    async with AsyncSessionLocal() as session:
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)

    return new_user


async def update_user(
    user_id: str | UUID,
    email: str | None = None,
    role: str | None = None,
    settings: dict | None = None,
    tenant_id: UUID | None = None,
    allow_system_admin: bool = False,
) -> UserModel | None:
    """Update user information, optionally filtered by tenant.

    ``role="SYSTEM_ADMIN"`` is refused unless ``allow_system_admin=True``
    (defense in depth — the API layer is the real gate; the service is
    the last chokepoint so a future caller cannot reintroduce the
    escalation by accident).
    """
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
        if role == Role.SYSTEM_ADMIN.value and not allow_system_admin:
            logger.warning(
                "update_user: refused SYSTEM_ADMIN role change for %s", user_id
            )
            return None
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


async def delete_user(user_id: str | UUID, tenant_id: UUID | None = None) -> bool:
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
