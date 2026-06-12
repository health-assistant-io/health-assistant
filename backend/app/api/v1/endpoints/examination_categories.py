from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, or_
from typing import List, Optional
from uuid import UUID
import re

from app.core.database import get_db
from app.core.security import get_current_user
from app.utils.svg import sanitize_svg
from app.models.examination_category import ExaminationCategory
from app.schemas.examination_category import (
    ExaminationCategoryCreate,
    ExaminationCategoryUpdate,
    ExaminationCategoryResponse,
)
from app.schemas.user import TokenData

router = APIRouter(prefix="/examination-categories", tags=["examination-categories"])


@router.get("", response_model=List[ExaminationCategoryResponse])
async def list_categories(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all available examination categories (global + tenant-specific)"""
    result = await db.execute(
        select(ExaminationCategory)
        .where(
            or_(
                ExaminationCategory.tenant_id == current_user.tenant_id,
                ExaminationCategory.tenant_id.is_(None),
            )
        )
        .order_by(ExaminationCategory.name.asc())
    )
    return result.scalars().all()


@router.post("", response_model=ExaminationCategoryResponse)
async def create_category(
    category_in: ExaminationCategoryCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new examination category"""
    # Check if name already exists
    existing = await db.execute(
        select(ExaminationCategory).where(
            ExaminationCategory.name.ilike(category_in.name)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category with this name already exists",
        )

    # Ensure slug is correctly formatted (no spaces, kebab-case)
    slug = re.sub(r"[^a-z0-9]+", "-", category_in.slug.lower()).strip("-")
    if not slug:
        # Generate slug from name if slug was invalid or empty
        slug = re.sub(r"[^a-z0-9]+", "-", category_in.name.lower()).strip("-")

    category_data = category_in.model_dump()
    category_data["slug"] = slug

    # Sanitize SVG icon if provided
    if category_data.get("icon") and category_data["icon"].get("type") == "custom_svg":
        category_data["icon"]["value"] = sanitize_svg(category_data["icon"]["value"])

    category = ExaminationCategory(**category_data, tenant_id=current_user.tenant_id)
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


@router.patch("/{category_id}", response_model=ExaminationCategoryResponse)
async def update_category(
    category_id: UUID,
    category_in: ExaminationCategoryUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an examination category"""
    result = await db.execute(
        select(ExaminationCategory).where(
            ExaminationCategory.id == category_id,
            or_(
                ExaminationCategory.tenant_id == current_user.tenant_id,
                ExaminationCategory.tenant_id.is_(None),
            ),
        )
    )
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    # Check permissions for global categories
    if category.tenant_id is None:
        # TODO: Implement super-admin check here.
        # For now, allow instance owners to customize standard categories.
        pass

    update_data = category_in.model_dump(exclude_unset=True)

    # Sanitize slug if it's being updated
    if "slug" in update_data:
        update_data["slug"] = re.sub(
            r"[^a-z0-9]+", "-", update_data["slug"].lower()
        ).strip("-")
    elif "name" in update_data and not category.slug:
        # This shouldn't happen with the new schema, but good for robustness
        update_data["slug"] = re.sub(
            r"[^a-z0-9]+", "-", update_data["name"].lower()
        ).strip("-")

    # Sanitize SVG icon if provided
    if update_data.get("icon") and update_data["icon"].get("type") == "custom_svg":
        update_data["icon"]["value"] = sanitize_svg(update_data["icon"]["value"])

    for key, value in update_data.items():
        setattr(category, key, value)

    await db.commit()
    await db.refresh(category)
    return category


@router.delete("/{category_id}")
async def delete_category(
    category_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an examination category"""
    result = await db.execute(
        select(ExaminationCategory).where(
            ExaminationCategory.id == category_id,
            ExaminationCategory.tenant_id == current_user.tenant_id,
        )
    )
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found or is global")

    await db.delete(category)
    await db.commit()
    return {"message": "Category deleted successfully"}
