"""Calendar connector persistence (ADR-0052 / plan 52 P2).

Three rows per concern — the same "one truth per concern" split the
connector framework uses:

**``calendar_bindings``** — one bound provider calendar: tenant-, user- and
patient-scoped (``UserIntegration`` scoping mirrored: ``patient_id`` is NOT
NULL so every projection query is patient-scoped structurally). The row is
the family **credential binding** shape (ADR-0052 §2): ``provider``,
``service``, ``auth_kind``, ``label``/``account_label``, the granted
``scopes`` (verbatim, consent data), the ``secret_ref`` **pointer**,
``direction``/``redaction`` policy and sync health. **No column is
token-shaped and none may be added** — credential material lives only in
``calendar_binding_credentials`` slots sealed with the family at-rest
cipher (``app.services.calendar.credentials``). ``account_label`` is
display metadata only — never a join key, never a second identity.

**``calendar_event_links``** — sync identity per projected record:
``(binding, uid)`` unique (the stable ``health:<kind>:<id>`` uid), the
remote ``remote_id``/``etag``/``content_hash`` pin, and the local record
coordinates (``local_kind``/``local_id``). Carries no PHI: a uid is an id.

**``calendar_binding_credentials``** — the sealed credential slots (same
shape and cipher as ``integration_credentials``, keyed by the binding
because a calendar binding is deliberately **not** a ``UserIntegration``
— plan 52 §3). See the custody module for the reuse rationale.

**``calendar_sync_logs``** — one audit row per sync run: counts and typed
error codes only. Never event titles, never attendee data, never
credentials (ADR-0052 §8).

Retention: bindings/links/credentials are ``record`` (erased with the
owning user/patient/binding via the cascade chain); sync logs are
``operational`` (bounded by the retention job's cleanup).
"""

import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin


#: Every vocabulary is an ENUM first (the source of truth) — the CHECK
#: constraints below derive from these, so a CHECK can never disagree with
#: the column type (test_enum_check_parity). `native_enum=False` keeps the
#: shipped shape (VARCHAR + CHECK) so no migration is needed.
class CalendarService(enum.StrEnum):
    CALENDAR = "calendar"


class CalendarAuthKind(enum.StrEnum):
    OAUTH2 = "oauth2"
    APP_PASSWORD = "app-password"
    TOKEN = "token"
    NONE = "none"


class CalendarDirection(enum.StrEnum):
    PUSH = "push"
    PULL = "pull"
    TWO_WAY = "two-way"


class CalendarRedaction(enum.StrEnum):
    """Health mapping law (ADR-0052 §6): ``neutral`` is the default."""

    NONE = "none"
    NEUTRAL = "neutral"
    FULL = "full"


class CalendarBindingStatus(enum.StrEnum):
    UNBOUND = "unbound"
    NEEDS_AUTH = "needs-auth"
    NEEDS_SECRET = "needs-secret"
    OK = "ok"
    DEGRADED = "degraded"
    FAILING = "failing"
    STALE = "stale"


class CalendarLocalKind(enum.StrEnum):
    EXAMINATION = "examination"
    CLINICAL_EVENT = "clinical_event"
    OCCURRENCE = "occurrence"


class CalendarSlotKind(enum.StrEnum):
    OAUTH = "oauth"
    PASSWORD = "password"
    TOKEN = "token"


class CalendarRunStatus(enum.StrEnum):
    """A sync run's OUTCOME (distinct from the binding's standing status)."""

    OK = "ok"
    FAILED = "failed"


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _values(cls: type) -> tuple[str, ...]:
    return tuple(member.value for member in cls)


DIRECTIONS = _values(CalendarDirection)
REDACTION_PROFILES = _values(CalendarRedaction)
AUTH_KINDS = _values(CalendarAuthKind)
BINDING_STATES = _values(CalendarBindingStatus)
LOCAL_KINDS = _values(CalendarLocalKind)
SLOT_KINDS = _values(CalendarSlotKind)
RUN_STATUSES = _values(CalendarRunStatus)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class CalendarBinding(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    """One bound provider calendar (family cloud-account row, ADR-0052 §2).

    ``secret_ref`` is the custody pointer (``calendar:<id>:<slot>``); the
    sealed material itself lives in ``calendar_binding_credentials``.
    ``options`` holds non-secret transport configuration (base URL, feed
    URL, calendar id/url, conflict/deletion policy overrides) — never a
    password, never a token. ``remote_calendar_id`` is the derived,
    protocol-keyed identity of the remote calendar (the calendar id for
    Google/Graph, the sanitized scheme+host+path collection/feed URL for
    CalDAV/ICS): the **one-writer-per-remote-calendar** law (ADR-0052 §9 /
    Rev 3 H4) pins a partial unique index on
    ``(tenant, patient, provider, remote_calendar_id)`` among *writable*
    bindings — two writers to one remote calendar are refused; read-only
    subscriptions may share freely.
    """

    __tablename__ = "calendar_bindings"

    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(50), nullable=False, index=True)
    service = Column(
        Enum(CalendarService, native_enum=False, values_callable=_enum_values),
        nullable=False,
        server_default=text("'calendar'"),
    )
    auth_kind = Column(
        Enum(CalendarAuthKind, native_enum=False, values_callable=_enum_values), nullable=False
    )
    label = Column(String(255), nullable=False)
    account_label = Column(String(255), nullable=True)
    scopes = Column(JSONB, nullable=True)
    secret_ref = Column(String(255), nullable=True)
    direction = Column(
        Enum(CalendarDirection, native_enum=False, values_callable=_enum_values),
        nullable=False,
        server_default=text("'push'"),
    )
    redaction = Column(
        Enum(CalendarRedaction, native_enum=False, values_callable=_enum_values),
        nullable=False,
        server_default=text("'neutral'"),
    )
    enabled = Column(Boolean, nullable=False, server_default=text("true"))
    status = Column(
        Enum(CalendarBindingStatus, native_enum=False, values_callable=_enum_values),
        nullable=False,
        server_default=text("'unbound'"),
    )
    last_error = Column(Text, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    consecutive_failures = Column(Integer, nullable=False, server_default=text("0"))
    sync_cursor = Column(Text, nullable=True)
    #: Derived remote-calendar identity (E/Rev 3 H4) — the unique-writer key.
    #: Carries no credential material: URLs enter sanitized (scheme+host+path).
    remote_calendar_id = Column(String(320), nullable=True, index=True)
    options = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    links = relationship(
        "CalendarEventLink",
        back_populates="binding",
        cascade="all, delete-orphan",
    )
    credentials = relationship(
        "CalendarBindingCredential",
        back_populates="binding",
        cascade="all, delete-orphan",
    )
    sync_logs = relationship(
        "CalendarSyncLog",
        back_populates="binding",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("service = 'calendar'", name="service_calendar_only"),
        CheckConstraint(f"auth_kind IN ({_in(AUTH_KINDS)})", name="auth_kind_vocabulary"),
        CheckConstraint(f"direction IN ({_in(DIRECTIONS)})", name="direction_vocabulary"),
        CheckConstraint(f"redaction IN ({_in(REDACTION_PROFILES)})", name="redaction_vocabulary"),
        CheckConstraint(f"status IN ({_in(BINDING_STATES)})", name="status_vocabulary"),
        CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_non_negative"),
        Index("ix_calendar_bindings_tenant_user_patient", "tenant_id", "user_id", "patient_id"),
        # One writer per remote calendar (ADR-0052 §9, Rev 3 H4): unique
        # among writable bindings; pull-only subscriptions may share.
        Index(
            "uq_calendar_bindings_remote_calendar",
            "tenant_id",
            "patient_id",
            "provider",
            "remote_calendar_id",
            unique=True,
            postgresql_where=text(
                "remote_calendar_id IS NOT NULL AND direction IN ('push', 'two-way')"
            ),
            sqlite_where=text(
                "remote_calendar_id IS NOT NULL AND direction IN ('push', 'two-way')"
            ),
        ),
    )


class CalendarEventLink(Base, UUIDMixin, TimestampMixin):
    """Sync identity for one projected record (ADR-0052 §5).

    ``(binding_id, uid)`` is unique — the uid is the stable
    ``health:examination:<id>`` / ``health:clinical-event:<id>`` /
    ``health:occurrence:<id>`` projection identity. ``local_hash``/
    ``remote_hash`` are the **separate** per-side content pins the re-closed
    contract requires (plan 52 §10 E3): providers normalize on write, so one
    shared hash reads as a phantom change on every run — a permanent update
    loop. No token column, no PHI (a uid is an id).
    """

    __tablename__ = "calendar_event_links"

    binding_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("calendar_bindings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    local_kind = Column(
        Enum(CalendarLocalKind, native_enum=False, values_callable=_enum_values), nullable=False
    )
    local_id = Column(PG_UUID(as_uuid=True), nullable=False)
    uid = Column(String(255), nullable=False)
    remote_id = Column(Text, nullable=True)
    etag = Column(Text, nullable=True)
    local_hash = Column(String(64), nullable=True)
    remote_hash = Column(String(64), nullable=True)

    binding = relationship("CalendarBinding", back_populates="links")

    __table_args__ = (
        CheckConstraint(f"local_kind IN ({_in(LOCAL_KINDS)})", name="local_kind_vocabulary"),
        UniqueConstraint("binding_id", "uid", name="uq_calendar_event_links_binding_uid"),
        Index("ix_calendar_event_links_binding_local", "binding_id", "local_id"),
    )


class CalendarBindingCredential(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """One sealed credential slot per ``(binding, slot kind)``.

    Same at-rest law as ``integration_credentials``: ``sealed_blob`` is the
    ``nx_auth.atrest`` cipher wrapper (``{"_encrypted": token, "_kid":
    fingerprint}``) sealed with the **binding id** as cipher context, so a
    copied blob cannot be replayed into another binding. ``kind`` is the
    slot vocabulary (``oauth|password|token``). Rotation reseals in place
    and stamps ``rotated_at``; revocation makes every read fail closed.

    The existing ``integration_credentials`` table cannot be reused
    directly: its owner column is a NOT NULL FK into ``user_integrations``,
    and a calendar binding is deliberately not a ``UserIntegration`` (plan
    52 §3). This table mirrors its shape one owner over — one cipher, one
    key ring, one custody mechanism (see
    ``app.services.calendar.credentials``).
    """

    __tablename__ = "calendar_binding_credentials"

    binding_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("calendar_bindings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(
        Enum(CalendarSlotKind, native_enum=False, values_callable=_enum_values), nullable=False
    )
    sealed_blob = Column(Text, nullable=False)
    kid = Column(String(64), nullable=False)
    scopes = Column(ARRAY(String(255)), nullable=False, server_default=text("'{}'::varchar[]"))
    access_expires_at = Column(DateTime(timezone=True), nullable=True)
    refresh_expires_at = Column(DateTime(timezone=True), nullable=True)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    binding = relationship("CalendarBinding", back_populates="credentials")

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(SLOT_KINDS)})", name="slot_kind_vocabulary"),
        UniqueConstraint("binding_id", "kind", name="uq_calendar_binding_credentials_slot"),
        Index("ix_calendar_binding_credentials_tenant_kid", "tenant_id", "kid"),
    )


class CalendarSyncLog(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """One sync run's outcome — the per-run audit row (ADR-0052 §8).

    ``counts`` carries the per-action tally (``create-remote``/``update-
    remote``/…); ``error_code`` is the typed charter/calendar code and
    ``error_detail`` the capped human detail. **Never** event titles,
    attendee data or credential material.

    Retention: ``operational``.
    """

    __tablename__ = "calendar_sync_logs"

    binding_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("calendar_bindings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(
        Enum(CalendarRunStatus, native_enum=False, values_callable=_enum_values),
        nullable=False,
        server_default=text("'ok'"),
    )
    counts = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    skipped = Column(Integer, nullable=False, server_default=text("0"))
    error_code = Column(String(24), nullable=True)
    error_detail = Column(Text, nullable=True)
    source = Column(String(16), nullable=False, server_default=text("'manual'"))
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)

    binding = relationship("CalendarBinding", back_populates="sync_logs")

    __table_args__ = (
        CheckConstraint(f"status IN ({_in(RUN_STATUSES)})", name="run_status_vocabulary"),
        Index("ix_calendar_sync_logs_binding_started", "binding_id", "started_at"),
    )
