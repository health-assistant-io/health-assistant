from typing import List, Optional
from uuid import UUID
import logging
from sqlalchemy import select, update, delete, and_
from app.models.patient_layout import PatientLayoutModel
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE

logger = logging.getLogger(__name__)


async def get_patient_layouts(
    user_id: UUID, patient_id: UUID
) -> List[PatientLayoutModel]:
    """Get all layouts for a user-patient pair"""
    if not DATABASE_AVAILABLE:
        return []

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PatientLayoutModel)
            .where(
                and_(
                    PatientLayoutModel.user_id == user_id,
                    PatientLayoutModel.patient_id == patient_id,
                )
            )
            .order_by(PatientLayoutModel.is_default.desc(), PatientLayoutModel.name)
        )
        return list(result.scalars().all())


async def get_active_layout(
    user_id: UUID, patient_id: UUID
) -> Optional[PatientLayoutModel]:
    """Get the active (default) layout for a user-patient pair"""
    if not DATABASE_AVAILABLE:
        return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PatientLayoutModel).where(
                and_(
                    PatientLayoutModel.user_id == user_id,
                    PatientLayoutModel.patient_id == patient_id,
                    PatientLayoutModel.is_default == True,
                )
            )
        )
        layout = result.scalar_one_or_none()

        if not layout:
            # Fallback to first available layout if no default is set
            result = await session.execute(
                select(PatientLayoutModel)
                .where(
                    and_(
                        PatientLayoutModel.user_id == user_id,
                        PatientLayoutModel.patient_id == patient_id,
                    )
                )
                .limit(1)
            )
            layout = result.scalar_one_or_none()

        return layout


async def create_patient_layout(
    user_id: UUID,
    patient_id: UUID,
    tenant_id: UUID,
    name: str,
    layout_config: dict,
    cards_config: list,
    is_default: bool = False,
) -> PatientLayoutModel:
    """Create a new patient layout"""
    if not DATABASE_AVAILABLE:
        return PatientLayoutModel(
            user_id=user_id,
            patient_id=patient_id,
            tenant_id=tenant_id,
            name=name,
            layout_config=layout_config,
            cards_config=cards_config,
            is_default=is_default,
        )

    async with AsyncSessionLocal() as session:
        if is_default:
            # Unset other defaults for this user-patient pair
            await session.execute(
                update(PatientLayoutModel)
                .where(
                    and_(
                        PatientLayoutModel.user_id == user_id,
                        PatientLayoutModel.patient_id == patient_id,
                    )
                )
                .values(is_default=False)
            )

        new_layout = PatientLayoutModel(
            user_id=user_id,
            patient_id=patient_id,
            tenant_id=tenant_id,
            name=name,
            layout_config=layout_config,
            cards_config=cards_config,
            is_default=is_default,
        )
        session.add(new_layout)
        await session.commit()
        await session.refresh(new_layout)
        return new_layout


async def update_patient_layout(
    layout_id: UUID, user_id: UUID, **kwargs
) -> Optional[PatientLayoutModel]:
    """Update an existing layout"""
    if not DATABASE_AVAILABLE:
        return None

    async with AsyncSessionLocal() as session:
        # Check if layout exists and belongs to user
        result = await session.execute(
            select(PatientLayoutModel).where(
                and_(
                    PatientLayoutModel.id == layout_id,
                    PatientLayoutModel.user_id == user_id,
                )
            )
        )
        layout = result.scalar_one_or_none()
        if not layout:
            return None

        if kwargs.get("is_default"):
            # Unset other defaults for this user-patient pair
            await session.execute(
                update(PatientLayoutModel)
                .where(
                    and_(
                        PatientLayoutModel.user_id == user_id,
                        PatientLayoutModel.patient_id == layout.patient_id,
                    )
                )
                .values(is_default=False)
            )

        for key, value in kwargs.items():
            if hasattr(layout, key) and value is not None:
                setattr(layout, key, value)

        await session.commit()
        await session.refresh(layout)
        return layout


async def delete_patient_layout(layout_id: UUID, user_id: UUID) -> bool:
    """Delete a layout"""
    if not DATABASE_AVAILABLE:
        return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(PatientLayoutModel).where(
                and_(
                    PatientLayoutModel.id == layout_id,
                    PatientLayoutModel.user_id == user_id,
                )
            )
        )
        await session.commit()
        return result.rowcount > 0
