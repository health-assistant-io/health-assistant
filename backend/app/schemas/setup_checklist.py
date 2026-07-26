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
    - ``completed`` is the AUTHORITATIVE effective state: ``evaluator_completed
      OR manually_completed``. The wizard treats it as truth.
    - ``manually_completed`` is ``True`` only when the user has explicitly
      toggled the step complete via the "mark as done" control (a manual
      override persisted in ``UserModel.settings``). When the evaluator already
      says complete this stays ``False`` — the override is only meaningful as a
      substitute for the data-derived state. The UI uses it to render an
      "Undo manual" affordance and a "marked manually" hint.
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
    manually_completed: bool = False
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


class ExtensionOption(BaseModel):
    """A coded picklist option for an extension value (e.g. an OMB race code)."""

    code: str
    display: str


class ExtensionCatalogItem(BaseModel):
    """One supported patient extension, surfaced so the client can render
    the correct input without hardcoding keys or CDC code lists.

    - ``value_type`` ``"omb_category"`` → render a dropdown of ``options``;
      the chosen option is written back as
      ``{ombCategory: {code, display, system}, text}``.
    - ``value_type`` ``"code"`` → render a dropdown of ``options`` (e.g.
      preferred_language); the value is the chosen ``code`` string.
    - ``value_type`` ``"string"`` → render a free-text input.
    """

    key: str = Field(..., description="Local extension key, e.g. 'race'")
    title_i18n_key: str
    value_type: str = Field(
        ..., description="omb_category | code | string"
    )
    cardinality: str = Field("0..1")
    options: Optional[List[ExtensionOption]] = Field(
        None, description="Picklist for omb_category / code value types"
    )


class ExtensionCatalogResponse(BaseModel):
    """The supported-extension catalog for an entity (patient today)."""

    entity: str = Field("patient")
    extensions: List[ExtensionCatalogItem]