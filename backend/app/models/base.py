from sqlalchemy import UUID, Column, DateTime, ForeignKey, Integer, MetaData, func, text
from sqlalchemy.orm import declarative_base

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        index=True,
    )


class TenantMixin:
    """Multi-tenant isolation column for tenant-owned rows.

    Every tenant-scoped row of a tenant-owned table carries a ``NOT NULL``
    ``tenant_id`` referencing ``tenants.id`` with ``ON DELETE CASCADE``, so
    tenant deletion purges all owned data. A table that genuinely mixes
    system/global rows with tenant rows uses :class:`SystemScopeTenantMixin`
    instead — nullable tenancy is an explicit, per-table decision, never a
    default.

    TimescaleDB hypertables cannot enforce FK constraints reliably, so
    ``TelemetryDataModel`` overrides ``tenant_id`` to drop the FK and relies
    on the named ``purge_orphaned_lifecycle_rows`` beat job plus the erasure
    walker instead (plan 6 §2.6).
    """

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class SystemScopeTenantMixin:
    """Nullable tenancy for tables that genuinely mix system and tenant rows.

    Use only where ``tenant_id IS NULL`` is the canonical representation of a
    system/global row (the seeded global taxonomy, global catalogs, global
    doctors, system-level audit events). Any table whose rows are all owned by
    a tenant uses :class:`TenantMixin` (``NOT NULL``) — constitution rule 5.
    """

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )


class UserMixin:
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)


class AuditMixin:
    created_by = Column(UUID(as_uuid=True), nullable=True)
    updated_by = Column(UUID(as_uuid=True), nullable=True)


class VersionedMixin:
    """Version column powering the FHIR R4 facade optimistic-lock primitive.

    ``version`` is bumped on every facade update (``app/facade/crud.update``),
    read for ``If-Match`` optimistic locking (HTTP 412), and exposed in the
    ``ETag`` header (``W/"<version>"``). It is NOT a full version-history
    implementation — the CapabilityStatement honestly declares
    ``versioning="no-version"`` (no ``vread`` / ``history-instance``).
    """

    version = Column(Integer, default=1)


class UUIDMixin:
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=text("gen_random_uuid()"),
        server_default=text("gen_random_uuid()"),
    )


class SoftDeleteMixin:
    """Tombstone column — the declared exception to the hard-delete default.

    Hard delete is the rule (constitution rule 10, plan 6 §2.4). ``deleted_at``
    exists on exactly one class of table: the FHIR R4-exposed resources, where
    the spec requires a deleted resource to answer ``410 Gone`` (not ``404``)
    forever, so the resource's existence must survive its deletion. The set is
    pinned by ``tests/test_schema_doctrine.py`` — adding or removing a
    tombstone table is a doctrine decision, not an incidental model edit.

    Tables that need proof-of-existence without a tombstone (``audit_events``,
    integration-proposal resolution traces, export/import job records) achieve
    it by being append-only / job records that are never hard-deleted; they do
    not carry ``deleted_at``.

    Resources mixing this in set ``deleted_at`` instead of calling
    ``session.delete()``; reads check ``deleted_at IS NULL``. The ``is_active``
    property is a convenience for query predicates
    (``Model.is_active`` → ``deleted_at IS NULL``).
    """

    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
