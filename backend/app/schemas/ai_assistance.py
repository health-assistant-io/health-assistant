from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, List
from uuid import UUID

from app.models.enums import HitlTaskStatus


class AIAssistanceRequest(BaseModel):
    task_type: str = Field(
        ...,
        description="The type of assistance requested (e.g., 'fill_biomarker_form', 'define_biomarker', 'define_medication', 'chat')",
    )
    user_input: str = Field(..., description="The natural language input from the user")
    reference_image: Optional[str] = Field(
        None, description="Optional base64 encoded image for reference (multimodal)"
    )
    context: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional context for the AI (e.g., patient_id, session_id)",
    )


class ChatMessageSchema(BaseModel):
    id: UUID
    role: str
    content: Dict[str, Any]
    tool_calls: Optional[List[Dict[str, Any]]] = None
    citations: Optional[List[str]] = None
    tasks: Optional[List[Dict[str, Any]]] = None
    created_at: Any

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class ChatSessionSchema(BaseModel):
    id: UUID
    title: Optional[str] = None
    patient_id: Optional[UUID] = None
    updated_at: Any

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class AIAssistanceResponse(BaseModel):
    task_type: str
    suggested_data: Optional[Dict[str, Any]] = None
    suggested_icons: Optional[List[str]] = None
    svg_content: Optional[str] = None
    justification: Optional[str] = None
    message: Optional[str] = None
    session_id: Optional[UUID] = None
    success: bool = True
    error: Optional[str] = None


class HitlResolutionRequest(BaseModel):
    """Body for confirming or dismissing a human-in-the-loop task card."""
    status: HitlTaskStatus = Field(
        ..., description="Whether the user confirmed or dismissed the proposal."
    )
    final_payload: Optional[Dict[str, Any]] = Field(
        None, description="The final (possibly user-edited) payload actually committed"
    )
    result: Optional[Dict[str, Any]] = Field(
        None, description="Outcome of the commit (e.g., created resource id)"
    )
    error: Optional[str] = Field(
        None, description="Error message if the commit failed (status should still be 'confirmed' attempt)"
    )


class HitlResumeRequest(BaseModel):
    """Body for triggering a HITL continuation turn after the user has resolved
    one or more proposed task cards. Selectors only — outcomes are read from
    the session's tasks JSONB on the server (never trusted from the client)."""
    message_id: Optional[UUID] = Field(
        None,
        description="Specific assistant message whose tasks should be summarized. "
        "If omitted, the most recent task-bearing message is used.",
    )

class AIAssistanceToolSchema(BaseModel):
    name: str
    description: str
    source: str = "built-in"
    schema_dict: Optional[Dict[str, Any]] = Field(None, alias="schema")
