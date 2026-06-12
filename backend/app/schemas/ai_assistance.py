from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from uuid import UUID


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
    created_at: Any

    class Config:
        from_attributes = True


class ChatSessionSchema(BaseModel):
    id: UUID
    title: Optional[str] = None
    patient_id: Optional[UUID] = None
    updated_at: Any

    class Config:
        from_attributes = True


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
