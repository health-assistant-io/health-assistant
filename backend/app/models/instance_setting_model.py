from sqlalchemy import Column, DateTime, String, Text, func

from app.models.base import Base


class InstanceSettingModel(Base):
    """Family-normative ``instance_settings`` (identity-auth §5).

    ``key`` TEXT PK (``auth_mode``, ``demo_mode``, …), ``value`` TEXT,
    ``updated_at``. Values are written **only** at initialization
    (identity-auth §4: first-boot / ``HA_AUTH_MODE`` + ``HA_DEMO_MODE``
    consumed on an empty DB only) — see ``app/core/instance_state.py``.
    Post-init reads are per-request and state-derived; unknown/missing
    values fail closed to ``authenticated`` / ``demo_mode=false``.
    """

    __tablename__ = "instance_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
