"""In-app guided setup-wizard checklist endpoint.

``GET /api/v1/setup/checklist`` returns a backend-derived
``SetupChecklistResponse`` for the calling user (role steps) plus, when
``entity`` + ``entity_id`` are supplied, the per-entity steps.

``GET /api/v1/setup/extension-catalog`` returns the supported patient
extension catalog (race / ethnicity / preferred_language /
insurance_provider) plus the OMB race/ethnicity/language picklist options,
so the wizard's extension section renders the correct inputs without
hardcoding keys or CDC codes.

Iteration 1 ships only the ``patient`` entity; the wizard UI lands in a
later iteration on the same branch. See ``dev/audits/setup-wizard-design.md``.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.setup_checklist import (
    ExtensionCatalogResponse,
    SetupChecklistResponse,
    StepResult,
)
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


@router.get(
    "/extension-catalog",
    response_model=ExtensionCatalogResponse,
)
async def get_extension_catalog(
    entity: str = Query(
        "patient",
        description="Entity scope: only 'patient' is supported in this iteration",
    ),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExtensionCatalogResponse:
    """Return the supported-extension catalog the wizard renders.

    Keeps the client free of hardcoded extension keys + CDC OMB code lists.
    """
    service = SetupChecklistService(db)
    return await service.get_extension_catalog(entity)


class ManualCompleteRequest(BaseModel):
    step_id: str = Field(..., description="Stable step id, e.g. 'system.first_tenant'")
    completed: bool = Field(
        ..., description="True to mark the step manually complete; False to clear."
    )
    entity: Optional[str] = Field(
        None, description="Entity scope ('patient') when toggling an entity step"
    )
    entity_id: Optional[UUID] = Field(
        None, description="Entity id (required when entity is given)"
    )


@router.post("/checklist/manual-complete", response_model=StepResult)
async def set_manual_complete(
    payload: ManualCompleteRequest,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StepResult:
    """Manually mark a wizard step complete (or clear the override).

    Lets a user dismiss a step the backend evaluator can't detect on its own
    ("I configured this differently", "this genuinely doesn't apply"). The
    override is persisted per-user in ``UserModel.settings`` and folds into
    the step's effective ``completed`` state on every checklist read.
    """
    if payload.entity is not None and payload.entity not in SUPPORTED_ENTITIES:
        from app.core.errors import ValidationError

        raise ValidationError(
            f"Unsupported checklist entity: {payload.entity}"
        )
    service = SetupChecklistService(db)
    return await service.set_manual_complete(
        current_user,
        payload.step_id,
        payload.completed,
        entity=payload.entity,
        entity_id=payload.entity_id,
    )