"""Pydantic schemas for the in-app guided-setup checklist.

Backend-derived (no onboarding_state table): see
``dev/audits/setup-wizard-design.md`` §D2. The checklist endpoint returns a
``SetupChecklistResponse``; the frontend wizard consumes ``steps`` + the
per-entity completion ratio to render the wizard / completion card.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StepResult(BaseModel):
    """One checklist step.

    - ``kind`` drives the UI step component:
      - ``redirect``       — step links elsewhere (e.g. "Create First Patient" → ``/patients?new``); the wizard polls the checklist on return.
      - ``inline_form``    — wizard renders a form inline (e.g. contact/telecom).
      - ``external_config``— deep link to a settings sub-page (e.g. AI config → ``/admin/ai``).
      - ``derived``        — read-only evaluation; wizard shows completion + a hint.
    - ``completed`` is authoritative (computed by the service from data).
    - ``optional`` steps do NOT count toward ``completion`` (mandatory-only).
    - ``payload_hint`` is freeform UI metadata (e.g. ``{"route": "/patients?new"}``).
    """

    id: str = Field(..., description="Stable step id, e.g. 'system.first_tenant'")
    entity: Optional[str] = Field(
        None, description="Entity scope, e.g. 'patient'; None for role steps"
    )
    title_i18n_key: str
    kind: str = Field(..., description="redirect | inline_form | external_config | derived")
    completed: bool = False
    optional: bool = False
    payload_hint: Optional[Dict[str, Any]] = None


class SetupChecklistResponse(BaseModel):
    role: str
    entity: Optional[str] = None
    entity_id: Optional[UUID] = None
    steps: List[StepResult]
    completion: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Mandatory-only progress (excludes optional steps).",
    )

    model_config = ConfigDict(from_attributes=True)