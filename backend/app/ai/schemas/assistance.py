# ruff: noqa: E501 -- long immutable strings; reflow when touched
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CodingSystem,
    ConceptRelationType,
    HitlTaskStatus,
)


class AIAssistanceRequest(BaseModel):
    task_type: str = Field(
        ...,
        description="The type of assistance requested (e.g., 'fill_biomarker_form', 'define_biomarker', 'define_medication', 'chat')",
    )
    user_input: str = Field(..., description="The natural language input from the user")
    reference_image: str | None = Field(
        None, description="Optional base64 encoded image for reference (multimodal)"
    )
    images: list[str] | None = Field(
        None,
        description=(
            "Optional list of image attachments (RFC 2397 data URLs, "
            "``data:image/...;base64,...``) for multimodal chat. Validated "
            "and capped by the backend. Only used for the ``chat`` task type."
        ),
    )
    context: dict[str, Any] | None = Field(
        default_factory=dict,
        description="Additional context for the AI (e.g., patient_id, session_id)",
    )


class ChatMessageSchema(BaseModel):
    id: UUID
    role: str
    content: dict[str, Any]
    tool_calls: list[dict[str, Any]] | None = None
    citations: list[str] | None = None
    tasks: list[dict[str, Any]] | None = None
    created_at: Any

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class ChatSessionSchema(BaseModel):
    id: UUID
    title: str | None = None
    patient_id: UUID | None = None
    updated_at: Any

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class AIAssistanceResponse(BaseModel):
    task_type: str
    suggested_data: dict[str, Any] | None = None
    suggested_icons: list[str] | None = None
    svg_content: str | None = None
    justification: str | None = None
    message: str | None = None
    session_id: UUID | None = None
    success: bool = True
    error: str | None = None


class HitlResolutionRequest(BaseModel):
    """Body for confirming or dismissing a human-in-the-loop task card."""

    status: HitlTaskStatus = Field(
        ..., description="Whether the user confirmed or dismissed the proposal."
    )
    final_payload: dict[str, Any] | None = Field(
        None, description="The final (possibly user-edited) payload actually committed"
    )
    result: dict[str, Any] | None = Field(
        None, description="Outcome of the commit (e.g., created resource id)"
    )
    error: str | None = Field(
        None,
        description="Error message if the commit failed (status should still be 'confirmed' attempt)",
    )


class HitlResumeRequest(BaseModel):
    """Body for triggering a HITL continuation turn after the user has resolved
    one or more proposed task cards. Selectors only — outcomes are read from
    the session's tasks JSONB on the server (never trusted from the client)."""

    message_id: UUID | None = Field(
        None,
        description="Specific assistant message whose tasks should be summarized. "
        "If omitted, the most recent task-bearing message is used.",
    )


class AIAssistanceToolSchema(BaseModel):
    name: str
    description: str
    source: str = "built-in"
    schema_dict: dict[str, Any] | None = Field(None, alias="schema")


# ---------------------------------------------------------------------------
# Structured-output models for AI-assisted form fillers / definitions / icons.
#
# These back ``llm.with_structured_output(...)`` for the non-chat task types
# dispatched by ``AIAssistanceService.assist``. They used to live inline at the
# top of ``app/ai/assistance/service.py``; consolidated here as part of the
# Phase 6c schemas consolidation.
# ---------------------------------------------------------------------------


class ExaminationMagicFillOutput(BaseModel):
    examination_date: str | None = Field(
        None, description="The date of the examination (ISO format YYYY-MM-DD)"
    )
    notes: str | None = Field(None, description="Clinical or doctor's notes")
    patient_notes: str | None = Field(None, description="Patient's notes or reasons for the visit")
    category: str | None = Field(None, description="The clinical category SLUG of the examination")
    doctor_names: list[str] = Field(default_factory=list, description="Names of doctors involved")


class BiomarkerFormOutput(BaseModel):
    biomarker_name: str | None = Field(
        None, description="The name of the biomarker identified (e.g. Glucose, WBC)"
    )
    value: float | None = Field(None, description="The numerical value of the biomarker")
    unit: str | None = Field(None, description="The unit symbol (e.g., mg/dL, mmol/L)")
    note: str | None = Field(None, description="A brief clinical note or observation")


class MedicationFormOutput(BaseModel):
    medication_name: str | None = Field(None, description="The name of the medication identified")
    dosage: str | None = Field(None, description="e.g., 500mg, 1 tablet")
    frequency_label: str | None = Field(
        None, description="Human readable frequency, e.g., 'Once Daily', 'Twice Daily'"
    )
    reason: str | None = Field(None, description="The reason for taking the medication")
    note: str | None = Field(None, description="Additional instructions or notes")


class BiomarkerDefinitionOutput(BaseModel):
    name: str = Field(..., description="The full clinical name of the biomarker")
    category: str = Field(..., description="Clinical category (e.g., Hematology, Metabolic)")
    unit_symbol: str = Field(..., description="Preferred unit (e.g., mg/dL, mmol/L)")
    coding_system: str = Field(
        "loinc",
        description="The medical coding system to use (loinc, snomed, or custom)",
    )
    code: str | None = Field(
        None,
        description="The specific code from the coding system (e.g., '2345-7' for LOINC glucose)",
    )
    aliases: list[str] = Field(default_factory=list, description="Common abbreviations")
    reference_range_min: float | None = Field(None, description="Lower bound")
    reference_range_max: float | None = Field(None, description="Upper bound")
    is_telemetry: bool = Field(
        False,
        description="Set to true if this metric is continuously tracked via IoT/wearables (e.g., heart rate, continuous glucose, steps)",
    )
    info: str = Field(..., description="Detailed clinical significance and info")


class MedicationDefinitionOutput(BaseModel):
    name: str = Field(..., description="Full name of the medication")
    description: str = Field(..., description="Brief overview of the medication")
    indications: str = Field(..., description="What the drug is used for")
    dosage_info: str = Field(..., description="Typical dosage instructions")
    contraindications: str = Field(..., description="When the drug should not be used")
    side_effects: list[str] = Field(default_factory=list, description="List of common side effects")


class CategoryIconSuggestionOutput(BaseModel):
    suggested_icons: list[str] = Field(
        ...,
        description="List of Lucide icon names (PascalCase, e.g. 'Activity', 'Droplet')",
    )


class CategoryIconGenerationOutput(BaseModel):
    svg_content: str = Field(..., description="Clean, minimalist SVG code for the icon")
    justification: str | None = Field(
        None, description="Short explanation of why this icon design was chosen"
    )


# ---------------------------------------------------------------------------
# Anatomy graph generation (backs the ``define_anatomy_graph`` task type).
# ---------------------------------------------------------------------------


class AnatomyImportNode(BaseModel):
    slug: str = Field(..., description="Unique kebab-case identifier (e.g., 'left-ventricle')")
    name: str = Field(..., description="Human readable name")
    class_concept_slug: str = Field(
        ...,
        description=(
            "Lowercase anatomy class slug: 'system', 'region', 'organ', "
            "'organ-part', 'tissue', 'joint', or 'other-anatomy'"
        ),
    )
    standard_system: CodingSystem | None = Field(
        None, description="Typically LOINC, SNOMED, or CUSTOM"
    )
    standard_code: str | None = Field(None, description="The official identifier code")
    description: str | None = Field(None, description="Brief description")
    is_custom: bool = Field(True, description="Always true for AI-generated")


class AnatomyImportEdge(BaseModel):
    source_slug: str = Field(..., description="Source node slug")
    target_slug: str = Field(..., description="Target node slug")
    relation_type: ConceptRelationType = Field(..., description="Type of relationship")


class AnatomyGraphDefinitionOutput(BaseModel):
    nodes: list[AnatomyImportNode] = Field(..., description="List of anatomical structures")
    edges: list[AnatomyImportEdge] = Field(
        ..., description="List of relationships between those structures"
    )
