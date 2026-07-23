"""OAuth2 client registration model.

An ``OAuthClient`` represents one external system granted access to the
FHIR R4 facade via the OAuth2 client-credentials grant. Each client is
bound to a single tenant and carries a set of SMART-on-FHIR scopes that
limit which FHIR resources / interactions it may perform on the facade.

Access tokens are **stateless JWTs** (see ``create_api_access_token`` in
``app.core.security``) — there is no ``oauth_access_tokens`` table. The
short token TTL (``OAUTH_ACCESS_TOKEN_TTL_MINUTES``) plus optional jti
revocation via ``token_store`` provide the lifecycle controls.

See ``docs/API_LAYERS.md`` for the layered model and
``docs/FHIR_R4_FACADE.md`` for the SMART scope vocabulary.
"""
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    String,
    UUID,
)
from sqlalchemy.dialects.postgresql import ARRAY

from app.models.base import Base, UUIDMixin, TimestampMixin


class OAuthClient(Base, UUIDMixin, TimestampMixin):
    """A registered external API consumer (FHIR facade client)."""

    __tablename__ = "oauth_clients"

    # Public client identifier (e.g. ``ci_<random>``). Distinct from the row
    # ``id`` so the public value carries no DB meaning and can be rotated
    # independently if ever needed.
    client_id = Column(String(64), unique=True, nullable=False, index=True)
    # bcrypt hash of the client secret (mirrors user password hashing).
    client_secret_hash = Column(String(255), nullable=False)
    # A client is bound to exactly one tenant; its api tokens inherit this
    # tenant and cannot cross the boundary (no role, no SYSTEM_ADMIN bypass).
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_name = Column(String(255), nullable=False)
    # SMART-on-FHIR scopes granted at registration (e.g.
    # ``["system/Observation.read", "system/Patient.read"]``). The token
    # endpoint intersects the requested scope with this set.
    scopes = Column(ARRAY(String), nullable=False, default=list)
    # For ``patient/`` scoped clients: the single patient the client may
    # touch. NULL for ``system/``-scoped (tenant-level) clients. Validated at
    # registration to belong to the client's tenant.
    bound_patient_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    is_confidential = Column(Boolean, nullable=False, server_default="true")
    is_active = Column(Boolean, nullable=False, server_default="true", index=True)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    def to_dict(self) -> dict:
        """Public representation — NEVER includes ``client_secret_hash``."""
        return {
            "id": self.id,
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "display_name": self.display_name,
            "scopes": list(self.scopes) if self.scopes else [],
            "bound_patient_id": self.bound_patient_id,
            "is_confidential": self.is_confidential,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
