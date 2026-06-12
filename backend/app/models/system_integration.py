from sqlalchemy import Column, String, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base

class SystemIntegration(Base):
    __tablename__ = "system_integrations"

    domain = Column(String(50), primary_key=True)
    is_enabled = Column(Boolean, default=False, nullable=False)
    global_config = Column(JSONB, nullable=True)
