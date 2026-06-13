from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging
from typing import List, Dict, Any

from app.core.database import get_db
from app.core.security import get_current_user, RoleChecker
from app.models.user_model import Role
from app.schemas.user import TokenData
from app.models.system_integration import SystemIntegration

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("", response_model=List[Dict[str, Any]])
async def list_system_integrations(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """List all system integration configurations, including newly discovered ones."""
    from app.core.integration_registry import integration_registry
    
    # Get all discovered manifests from the filesystem
    manifests = integration_registry.get_all_manifests()
    
    # Get all database records
    stmt = select(SystemIntegration)
    result = await db.execute(stmt)
    db_integrations = {i.domain: i for i in result.scalars().all()}
    
    response = []
    
    # First, list all discovered integrations (combining DB state if it exists)
    discovered_domains = set()
    for manifest in manifests:
        domain = manifest.get("domain")
        if domain:
            discovered_domains.add(domain)
            db_record = db_integrations.get(domain)
            response.append({
                "domain": domain,
                "name": manifest.get("name", domain),
                "version": manifest.get("version", "Unknown"),
                "is_enabled": db_record.is_enabled if db_record else False
            })
            
    # Then, add any stale DB records that might no longer exist on the filesystem
    for domain, record in db_integrations.items():
        if domain not in discovered_domains:
            response.append({
                "domain": domain,
                "name": domain,
                "version": "Unknown (Not on filesystem)",
                "is_enabled": record.is_enabled
            })
            
    return response

@router.post("/{domain}/enable")
async def enable_system_integration(
    domain: str,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Enable an integration globally across the system."""
    stmt = select(SystemIntegration).where(SystemIntegration.domain == domain)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.is_enabled = True
    else:
        new_integration = SystemIntegration(domain=domain, is_enabled=True)
        db.add(new_integration)
        
    await db.commit()
    
    # Trigger a registry reload to make it immediately available
    from app.core.integration_registry import integration_registry
    await integration_registry.initialize(db)
    
    return {"message": f"Integration {domain} enabled successfully."}

@router.post("/{domain}/disable")
async def disable_system_integration(
    domain: str,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Disable an integration globally across the system."""
    stmt = select(SystemIntegration).where(SystemIntegration.domain == domain)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.is_enabled = False
        await db.commit()
        
    return {"message": f"Integration {domain} disabled successfully."}
