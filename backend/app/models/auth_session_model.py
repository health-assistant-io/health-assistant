from sqlalchemy import Column, DateTime, ForeignKey, String, UUID, func
from app.models.base import Base, UUIDMixin


class AuthSessionModel(Base, UUIDMixin):
    """Family-normative ``auth_sessions`` (identity-auth §5) — refresh families.

    One row per signed-in device ("family"): the refresh token rotates on
    every use and the row stores the sha256 of the *current* refresh jti
    (never a raw token). ``expires_at`` is the 7-day rolling window,
    ``absolute_expires_at`` the 30-day hard cap regardless of activity.
    Logout / reuse detection revoke the row (``revoked_at``).

    ``created_at`` is the sanctioned product addition (§5) — the device
    list surface (``GET /api/v1/me/sessions``) shows when each family
    signed in.

    The Redis jti store (``app/core/token_store.py``) remains the hot
    revocation layer; this table is the authoritative family record.
    """

    __tablename__ = "auth_sessions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_jti_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    absolute_expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    client_label = Column(String(200), nullable=False, default="", server_default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "client_label": self.client_label,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "absolute_expires_at": (
                self.absolute_expires_at.isoformat() if self.absolute_expires_at else None
            ),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }
