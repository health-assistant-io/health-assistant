from typing import Optional, Dict, Any
from uuid import UUID
import logging
import secrets
from sqlalchemy import select, update, delete, func
from app.models.tenant_model import TenantModel
from app.models.fhir.organization import OrganizationModel
from app.models.enums import OrganizationType
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.utils.slug import slugify

logger = logging.getLogger(__name__)


async def get_tenant(tenant_id: str | UUID) -> Optional[TenantModel]:
    """Get tenant by ID"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(tenant_id, str):
        try:
            tenant_id = UUID(tenant_id)
        except ValueError:
            return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TenantModel).where(TenantModel.id == tenant_id)
        )
        return result.scalar_one_or_none()


async def create_tenant(
    name: str,
    settings: dict = None,
    slug: Optional[str] = None,
) -> Optional[TenantModel]:
    """Create a new tenant and a default root organization.

    The ``slug`` column is NOT NULL + UNIQUE, so when a slug isn't supplied
    we derive one from the name and guarantee uniqueness by appending a
    short random suffix on collision.
    """
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for create_tenant")
        return None

    async with AsyncSessionLocal() as session:
        base_slug = slugify(slug) if slug else slugify(name)

        # Guarantee uniqueness within the tenants table.
        candidate = base_slug
        for _ in range(8):
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(TenantModel)
                    .where(TenantModel.slug == candidate)
                )
            ).scalar() or 0
            if count == 0:
                break
            candidate = f"{base_slug}-{secrets.token_hex(2)}"
        else:
            candidate = f"{base_slug}-{secrets.token_hex(4)}"

        new_tenant = TenantModel(
            name=name, slug=candidate, settings=settings or {}
        )
        session.add(new_tenant)
        await session.flush()  # Get ID for new_tenant

        # Auto-provision root organization
        root_org = OrganizationModel(
            tenant_id=new_tenant.id,
            name=f"{name} Household",
            org_type=OrganizationType.HOUSEHOLD,
            active=True,
        )
        session.add(root_org)

        await session.commit()
        await session.refresh(new_tenant)

    return new_tenant


async def update_tenant(
    tenant_id: str | UUID, name: str = None, settings: dict = None
) -> Optional[TenantModel]:
    """Update tenant information"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(tenant_id, str):
        try:
            tenant_id = UUID(tenant_id)
        except ValueError:
            return None

    update_data = {}
    if name:
        update_data["name"] = name
    if settings is not None:
        update_data["settings"] = settings

    if not update_data:
        return await get_tenant(tenant_id)

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(TenantModel).where(TenantModel.id == tenant_id).values(**update_data)
        )
        await session.commit()

    return await get_tenant(tenant_id)


async def delete_tenant(tenant_id: str | UUID) -> bool:
    """Delete tenant (hard delete for now)"""
    if not DATABASE_AVAILABLE:
        return False

    if isinstance(tenant_id, str):
        try:
            tenant_id = UUID(tenant_id)
        except ValueError:
            return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(TenantModel).where(TenantModel.id == tenant_id)
        )
        await session.commit()
        return result.rowcount > 0


async def list_tenants(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """List all tenants"""
    if not DATABASE_AVAILABLE:
        return {"items": [], "total": 0}

    async with AsyncSessionLocal() as session:
        query = select(TenantModel)

        # Get total count
        from sqlalchemy import func

        count_query = select(func.count()).select_from(query.subquery())
        total = await session.execute(count_query)
        total_count = total.scalar() or 0

        # Apply pagination
        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        items = result.scalars().all()

        return {"items": [item.to_dict() for item in items], "total": total_count}
