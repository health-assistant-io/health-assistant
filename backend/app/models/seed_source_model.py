"""The seed-pack source lane (ADR 0056 persistence).

Six tables backing the data-only pack lane: where packs come from, the
immutable digest-pinned versions, the plan/apply run history (with per-item
outcomes), the per-row provenance, and the deferred-reference retry queue.
Packs are data, never executed; every remote version is pinned by its
content digest and approved before any apply (``guidelines/security.md`` —
the seed-pack lane section is the canonical law).

``seed_sources``
    One registered origin of seed packs: ``kind``
    (``bundled|file|git|integration|registry``), ``uri``, the pinned
    ``ref_spec``/``subpath``, the precedence ``priority`` (bundled 0 <
    curated remote 100 < tenant overlay 200+) and ``scope``
    (``system|tenant``). ``auth_secret_ref`` names a secret SLOT (never a
    value). ``tenant_id`` is nullable ``ON DELETE CASCADE`` — NULL is a
    system-level source, a tenant overlay source dies with its tenant.
    ``current_version_id`` points at the approved version (``SET NULL``:
    revoking the last approved version leaves the source without a current
    one instead of dangling). Fetch failures land in
    ``last_check_at``/``last_error`` and never move the pins.

``seed_source_versions``
    **Immutable** resolved packs: ``digest`` (the whole-pack content digest —
    the approval anchor), ``commit_sha``/``etag`` (the transport pin that
    resolved it), the ``manifest`` JSONB and ``contract_version`` (the pack
    schema version the manifest declares). A digest change is a NEW row
    requiring re-approval; content columns are never updated in place.
    ``status`` walks ``pending → approved/rejected`` (``revoked`` pulls an
    approved digest back); a rejected version keeps its typed validation
    problems in ``reason``. Unique per ``(source_id, digest)``.

``seed_import_runs`` / ``seed_import_run_items``
    The plan/apply history per version: ``mode`` (``plan|apply``), ``status``
    (``running|ok|failed|partial|conflicted``), the ``plan`` diff document
    and ``stats``. Items record the per-item outcome (action +
    ``content_hash`` + the ``conflict`` payload of a losing/overridden side)
    — the durable history the single provenance row cannot hold.

``seed_item_provenance``
    One row per seeded catalog item — ``(entity_type, entity_id)`` unique —
    naming the ``source_version_id`` that last wrote it, its ``seed_key``,
    the applied ``content_hash``, the lifecycle ``state``
    (``active|retired|overridden``) and ``user_modified`` (a user edit
    freezes auto-updates; "update available" instead, ADR 0056 D3). The
    entity row itself carries tenancy; the version FK is ``SET NULL`` so
    provenance survives even a removed source.

``seed_deferred_refs``
    Cross-pack references that could not resolve at apply time: the typed
    ``payload`` to retry, ``attempts``/``last_error`` and the ``status``
    (``pending`` rows are retried on every new approved version — never
    silently skipped; ADR 0056 D5/D10).
"""

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import func

from app.models.base import Base, SystemScopeTenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import (
    SeedDeferredStatus,
    SeedItemAction,
    SeedItemState,
    SeedRunMode,
    SeedRunStatus,
    SeedSourceKind,
    SeedSourceScope,
    SeedVersionStatus,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


class SeedSource(Base, UUIDMixin, SystemScopeTenantMixin, TimestampMixin):
    """One registered origin of seed packs (ADR 0056)."""

    __tablename__ = "seed_sources"

    kind = Column(
        SQLEnum(
            SeedSourceKind,
            name="seed_source_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    uri = Column(String(2048), nullable=False)
    ref_spec = Column(String(256), nullable=True)
    subpath = Column(String(512), nullable=True)
    priority = Column(Integer, nullable=False, server_default=text("0"))
    scope = Column(
        SQLEnum(
            SeedSourceScope,
            name="seed_source_scope",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=8,
        ),
        nullable=False,
        server_default=text("'system'"),
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    auth_secret_ref = Column(String(256), nullable=True)
    current_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("seed_source_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    last_check_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("priority >= 0 AND priority <= 999", name="priority_range"),
        CheckConstraint(
            "(scope = 'system' AND tenant_id IS NULL) OR "
            "(scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="scope_tenant_match",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "kind": self.kind.value if self.kind else None,
            "uri": self.uri,
            "ref_spec": self.ref_spec,
            "subpath": self.subpath,
            "priority": self.priority,
            "scope": self.scope.value if self.scope else None,
            "enabled": bool(self.enabled),
            "auth_secret_ref": self.auth_secret_ref,
            "current_version_id": str(self.current_version_id) if self.current_version_id else None,
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SeedSourceVersion(Base, UUIDMixin, TimestampMixin):
    """One immutable digest-pinned pack version of a source (ADR 0056 D4)."""

    __tablename__ = "seed_source_versions"

    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("seed_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    commit_sha = Column(String(64), nullable=True)
    etag = Column(String(128), nullable=True)
    digest = Column(String(64), nullable=False)
    manifest = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(
        SQLEnum(
            SeedVersionStatus,
            name="seed_version_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        default=SeedVersionStatus.PENDING,
        server_default=text("'pending'"),
        index=True,
    )
    reason = Column(Text, nullable=True)
    approved_by = Column(UUID(as_uuid=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    contract_version = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "digest", name="uq_seed_source_versions_source_digest"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "source_id": str(self.source_id),
            "commit_sha": self.commit_sha,
            "etag": self.etag,
            "digest": self.digest,
            "status": self.status.value if self.status else None,
            "reason": self.reason,
            "approved_by": str(self.approved_by) if self.approved_by else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "contract_version": self.contract_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SeedImportRun(Base, UUIDMixin, TimestampMixin):
    """One plan or apply of one pack version (the durable run record)."""

    __tablename__ = "seed_import_runs"

    source_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("seed_source_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode = Column(
        SQLEnum(
            SeedRunMode,
            name="seed_run_mode",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=8,
        ),
        nullable=False,
    )
    status = Column(
        SQLEnum(
            SeedRunStatus,
            name="seed_run_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        default=SeedRunStatus.RUNNING,
        server_default=text("'running'"),
        index=True,
    )
    plan = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    stats = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "source_version_id": str(self.source_version_id),
            "mode": self.mode.value if self.mode else None,
            "status": self.status.value if self.status else None,
            "error": self.error,
            "stats": self.stats or {},
            "plan": self.plan or {},
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SeedImportRunItem(Base, UUIDMixin, TimestampMixin):
    """The per-item outcome of one run (the loser/override record)."""

    __tablename__ = "seed_import_run_items"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("seed_import_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    seed_key = Column(String(128), nullable=True)
    action = Column(
        SQLEnum(
            SeedItemAction,
            name="seed_item_action",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    content_hash = Column(String(64), nullable=True)
    conflict = Column(JSONB, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "run_id": str(self.run_id),
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "seed_key": self.seed_key,
            "action": self.action.value if self.action else None,
            "content_hash": self.content_hash,
            "conflict": self.conflict or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SeedItemProvenance(Base, UUIDMixin, TimestampMixin):
    """Which pack version last wrote a seeded catalog item (ADR 0056 D3/D5)."""

    __tablename__ = "seed_item_provenance"

    entity_type = Column(String(64), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    source_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("seed_source_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    seed_key = Column(String(128), nullable=True, index=True)
    content_hash = Column(String(64), nullable=True)
    state = Column(
        SQLEnum(
            SeedItemState,
            name="seed_item_state",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        default=SeedItemState.ACTIVE,
        server_default=text("'active'"),
        index=True,
    )
    user_modified = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    applied_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_seed_item_provenance_entity"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id),
            "source_version_id": str(self.source_version_id) if self.source_version_id else None,
            "seed_key": self.seed_key,
            "content_hash": self.content_hash,
            "state": self.state.value if self.state else None,
            "user_modified": bool(self.user_modified),
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SeedDeferredRef(Base, UUIDMixin, TimestampMixin):
    """A cross-pack reference that could not resolve yet — retried, never
    silently skipped (ADR 0056 D5/D10)."""

    __tablename__ = "seed_deferred_refs"

    source_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("seed_source_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    last_error = Column(Text, nullable=True)
    status = Column(
        SQLEnum(
            SeedDeferredStatus,
            name="seed_deferred_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        default=SeedDeferredStatus.PENDING,
        server_default=text("'pending'"),
        index=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "source_version_id": str(self.source_version_id),
            "payload": self.payload or {},
            "attempts": self.attempts,
            "last_error": self.last_error,
            "status": self.status.value if self.status else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
