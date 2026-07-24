"""In-app guided setup-wizard checklist endpoint.

``GET /api/v1/setup/checklist`` returns a backend-derived
``SetupChecklistResponse`` for the calling user (role steps) plus, when
``entity`` + ``entity_id`` are supplied, the per-entity steps.

Iteration 1 ships only the ``patient`` entity; the wizard UI is a later
commit on the same branch. See ``dev/audits/setup-wizard-design.md``.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.setup_checklist import SetupChecklistResponse
from app.schemas.user import TokenData
from app.services.setup_checklist_service import (
    SUPPORTED_ENTITIES,
    SetupChecklistService,
)

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/checklist", response_model=SetupChecklistResponse)
async def get_setup_checklist(
    entity: Optional[str] = Query(
        None, description="Entity scope: one of 'patient' (iteration 1)"
    ),
    entity_id: Optional[UUID] = Query(
        None, description="Entity id (required when entity is given)"
    ),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SetupChecklistResponse:
    if entity is not None and entity not in SUPPORTED_ENTITIES:
        from app.core.errors import ValidationError

        raise ValidationError(
            f"Unsupported checklist entity: {entity}"
        )
    service = SetupChecklistService(db)
    return await service.get_checklist(
        current_user, entity=entity, entity_id=entity_id
    )