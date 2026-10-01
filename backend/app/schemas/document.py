from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentBase(BaseModel):
    filename: str
    patient_id: UUID | None = None
    examination_id: UUID | None = None
    include_in_extraction: bool = False


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(BaseModel):
    status: str | None = None
    progress: int | None = None
    extracted_text: str | None = None
    entities: Any | None = None
    examination_id: UUID | None = None
    include_in_extraction: bool | None = None


class DocumentEdit(BaseModel):
    crop_left: int | None = None
    crop_top: int | None = None
    crop_right: int | None = None
    crop_bottom: int | None = None
    perspective_points: list[tuple[int, int]] | None = None
    brightness: float = 1.0
    contrast: float = 1.0
    sharpness: float = 1.0
    rotation: int = 0


class DocumentResponse(DocumentBase):
    id: UUID
    owner_id: UUID
    status: str
    progress: int
    error_message: str | None = None
    file_path: str
    include_in_extraction: bool
    extracted_text: str | None = None
    entities: Any | None = None
    examination_id: UUID | None = None
    parent_id: UUID | None = None
    is_edited: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
