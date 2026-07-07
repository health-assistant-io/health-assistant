from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, UUIDMixin, AuditMixin, TimestampMixin


class SystemSetting(Base, UUIDMixin, AuditMixin, TimestampMixin):
    __tablename__ = "system_settings"

    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(JSONB, nullable=False)

    @classmethod
    async def get_value(cls, db, key, default=None):
        from sqlalchemy import select

        result = await db.execute(select(cls.value).where(cls.key == key))
        val = result.scalar_one_or_none()
        return val if val is not None else default

    @classmethod
    async def set_value(cls, db, key, value, user_id=None):
        from sqlalchemy import select

        existing = await db.execute(select(cls).where(cls.key == key))
        obj = existing.scalars().first()
        if obj:
            obj.value = value
            if user_id:
                obj.updated_by = user_id
        else:
            db.add(cls(key=key, value=value, created_by=user_id, updated_by=user_id))
        await db.commit()
