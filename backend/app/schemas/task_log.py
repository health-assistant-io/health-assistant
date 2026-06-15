from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, Dict
from uuid import UUID
from datetime import datetime


class TaskLogResponse(BaseModel):
    id: UUID
    task_name: str
    task_id: str
    resource_id: Optional[UUID] = None
    level: str
    stage: Optional[str] = None
    message: str
    data: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
