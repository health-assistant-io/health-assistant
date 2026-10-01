from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TaskLogResponse(BaseModel):
    id: UUID
    task_name: str
    task_id: str
    resource_id: UUID | None = None
    level: str
    stage: str | None = None
    message: str
    data: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
