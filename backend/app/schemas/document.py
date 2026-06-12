from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional, Any, List, Tuple
from datetime import datetime


class DocumentBase(BaseModel):
    filename: str
    patient_id: Optional[UUID] = None
    examination_id: Optional[UUID] = None
    include_in_extraction: bool = False


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(BaseModel):
    status: Optional[str] = None
    progress: Optional[int] = None
    extracted_text: Optional[str] = None
    entities: Optional[Any] = None
    examination_id: Optional[UUID] = None
    include_in_extraction: Optional[bool] = None


class DocumentEdit(BaseModel):
    crop_left: Optional[int] = None
    crop_top: Optional[int] = None
    crop_right: Optional[int] = None
    crop_bottom: Optional[int] = None
    perspective_points: Optional[List[Tuple[int, int]]] = None
    brightness: float = 1.0
    contrast: float = 1.0
    sharpness: float = 1.0
    rotation: int = 0


class DocumentResponse(DocumentBase):
    id: UUID
    owner_id: UUID
    status: str
    progress: int
    error_message: Optional[str] = None
    file_path: str
    include_in_extraction: bool
    extracted_text: Optional[str] = None
    entities: Optional[Any] = None
    examination_id: Optional[UUID] = None
    parent_id: Optional[UUID] = None
    is_edited: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
