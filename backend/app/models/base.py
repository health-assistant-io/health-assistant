from sqlalchemy import Column, DateTime, func, text, Integer, Boolean, UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        index=True,
    )


class TenantMixin:
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)


class UserMixin:
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)


class AuditMixin:
    created_by = Column(UUID(as_uuid=True), nullable=True)
    updated_by = Column(UUID(as_uuid=True), nullable=True)


class VersionedMixin:
    version = Column(Integer, default=1)
    is_current = Column(Boolean, default=True)


class UUIDMixin:
    id = Column(UUID(as_uuid=True), primary_key=True, default=text("gen_random_uuid()"))


class SoftDeleteMixin:
    """Soft-delete column for FHIR-exposed resources.

    FHIR spec requires that a deleted resource return ``410 Gone`` (not
    ``404 Not Found``) so callers can distinguish "never existed" from
    "was deleted". Hard deletes lose that signal. Resources mixing this in
    set ``deleted_at`` instead of calling ``session.delete()``; reads check
    ``deleted_at IS NULL``.

    The ``is_active`` property is a convenience for query predicates
    (``Model.is_active`` → ``deleted_at IS NULL``). Note this is distinct
    from :class:`VersionedMixin.is_current`, which tracks versioning state.
    """

    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
