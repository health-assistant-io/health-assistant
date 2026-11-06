"""The imported-extension source lane (plan 7 — ADR-0040 persistence).

Four tables backing the T2/T3 import lane: where content comes from, the
immutable resolved versions, who consented to what, and the isolation
posture evidence. All digest-pinned and fail-closed; the family charter
(``the family extension standard``) is the law — these rows are health's
persistence for it.

``extension_sources``
    One registered origin of imported content: ``kind``
    (``git|file|registry``), the ``uri``, the pinned ``ref_spec``, and
    ``trust_tier`` (``T2|T3`` — the lane's code-only scope; declarative T0
    data never rides sources). ``auth_secret_ref`` names a secret SLOT
    (never a value — inline tokens are structurally invalid per the x-secret
    law). ``tenant_id`` is nullable ``ON DELETE CASCADE``: NULL is a
    system-level source (the genuinely mixed-scope case), a tenant row dies
    with its tenant. Fetch failures land in ``last_check_at``/``last_error``
    and never move the pins.

``extension_source_versions``
    **Immutable** resolved content: ``commit_sha``/``digest`` (the content
    digest — the consent anchor), ``manifest_digest``, the ``envelope`` JSONB
    and ``contract_version``. A digest change is a NEW row requiring
    re-approval — content columns are never updated in place. ``status``
    walks ``pending → approved/rejected`` (``revoked`` pulls an approved
    digest back); a rejected version keeps its typed registration problems in
    ``reason`` — self-disable is a row, never a log-and-skip (plan 37 6.1).
    Tenancy rides the parent source (FK cascade).

``extension_consents``
    Who allowed what, version-bound: one row per ``(user, version,
    capability)`` with ``decision`` (``granted|denied``) and the optional
    ``scope_json`` payload. Consent binds to a manifest DIGEST via
    ``version_id`` — an upgraded extension re-prompts for what is new while
    unchanged capabilities carry their grants forward as new rows.

``extension_isolation_posture``
    Runtime evidence per (version, host profile): the source's
    ``requested_tier`` next to ``derived_tier`` (``contract.derive_tier`` is
    the one derivation — a contradiction is queryable, never silently
    retiered), and the ``posture`` JSONB (the graded containment result from
    ``app.extensions.isolation`` DETECTION, never a constant).
    ``operational`` — bounded retention.
"""

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import func

from app.models.base import Base, SystemScopeTenantMixin, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import (
    ExtensionConsentDecision,
    ExtensionSourceKind,
    ExtensionSourceTrustTier,
    ExtensionTier,
    ExtensionVersionStatus,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


class ExtensionSource(Base, UUIDMixin, SystemScopeTenantMixin, TimestampMixin):
    """One registered origin of imported extension content."""

    __tablename__ = "extension_sources"

    kind = Column(
        SQLEnum(
            ExtensionSourceKind,
            name="extension_source_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    uri = Column(String(2048), nullable=False)
    ref_spec = Column(String(256), nullable=True)
    auth_secret_ref = Column(String(256), nullable=True)
    trust_tier = Column(
        SQLEnum(
            ExtensionSourceTrustTier,
            name="extension_source_trust_tier",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=8,
        ),
        nullable=False,
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_check_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "kind": self.kind.value if self.kind else None,
            "uri": self.uri,
            "ref_spec": self.ref_spec,
            "auth_secret_ref": self.auth_secret_ref,
            "trust_tier": self.trust_tier.value if self.trust_tier else None,
            "enabled": bool(self.enabled),
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExtensionSourceVersion(Base, UUIDMixin, TimestampMixin):
    """One immutable resolved content digest of a source (the consent anchor)."""

    __tablename__ = "extension_source_versions"

    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("extension_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    commit_sha = Column(String(64), nullable=True)
    digest = Column(String(64), nullable=False)
    manifest_digest = Column(String(64), nullable=False)
    envelope = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(
        SQLEnum(
            ExtensionVersionStatus,
            name="extension_version_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        default=ExtensionVersionStatus.PENDING,
        server_default=text("'pending'"),
        index=True,
    )
    reason = Column(Text, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    contract_version = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "digest", name="uq_extension_source_versions_source_digest"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "source_id": str(self.source_id),
            "commit_sha": self.commit_sha,
            "digest": self.digest,
            "manifest_digest": self.manifest_digest,
            "status": self.status.value if self.status else None,
            "reason": self.reason,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "approved_by": str(self.approved_by) if self.approved_by else None,
            "contract_version": self.contract_version,
            "capabilities": sorted((self.envelope or {}).get("capabilities") or []),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExtensionConsent(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """One principal's decision on one capability of one content digest."""

    __tablename__ = "extension_consents"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("extension_source_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability = Column(String(64), nullable=False)
    decision = Column(
        SQLEnum(
            ExtensionConsentDecision,
            name="extension_consent_decision",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    scope_json = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    decided_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_by = Column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "version_id", "capability", name="uq_extension_consents_user_version_cap"
        ),
        CheckConstraint("capability <> ''", name="capability_not_empty"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id),
            "version_id": str(self.version_id),
            "capability": self.capability,
            "decision": self.decision.value if self.decision else None,
            "scope_json": self.scope_json or {},
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decided_by": str(self.decided_by) if self.decided_by else None,
        }


class ExtensionIsolationPosture(Base, UUIDMixin, TimestampMixin):
    """Runtime isolation evidence for one version on one host profile."""

    __tablename__ = "extension_isolation_posture"

    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("extension_source_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    host_profile = Column(String(64), nullable=False)
    requested_tier = Column(
        SQLEnum(
            ExtensionTier,
            name="extension_posture_requested_tier",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=8,
        ),
        nullable=False,
    )
    derived_tier = Column(
        SQLEnum(
            ExtensionTier,
            name="extension_posture_derived_tier",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=8,
        ),
        nullable=False,
    )
    posture = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    probed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "version_id", "host_profile", name="uq_extension_isolation_posture_version_host"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "version_id": str(self.version_id),
            "host_profile": self.host_profile,
            "requested_tier": self.requested_tier.value if self.requested_tier else None,
            "derived_tier": self.derived_tier.value if self.derived_tier else None,
            "posture": self.posture or {},
            "probed_at": self.probed_at.isoformat() if self.probed_at else None,
        }
