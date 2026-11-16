"""AI task definitions (seeded lookup) and the per-AI-call audit table.

Two tables, one theme — the task vocabulary and its call history (plan 4):

``ai_task_defs``
    The *extensible domain vocabulary* (constitution rule 7b) behind
    ``ai_task_assignments.task_type_id`` and ``ai_call_logs.task_type_id``.
    Rows are seeded at boot from the one TaskDef registry
    (:mod:`app.ai.providers.task_defs`) — seeding is data, not DDL — so adding
    a task is a registry entry plus a row, never a migration. This is why the
    task vocabulary is NOT a CHECK constraint: a registry + CHECK + contract
    JSON would be three sources of truth and turn every new task into DDL.
    Rows are never deleted while referenced (FK ``RESTRICT``).

``ai_call_logs``
    One row per model call, written at the one choke point (the LangChain model
    factory + the decision funnel). This is the *operational* audit trail —
    token/cost/latency history, error classification, and the "why did the model
    say this" correlation via ``trace_id``.

    Lifecycle: ``operational`` (bounded retention owned by the plan-6 retention
    job). Erasure: ``tenant_id`` cascades on tenant deletion;
    ``user_id``/``patient_id`` are deliberately plain columns (no FK) so the
    audit row outlives config and patient rows — deleting a provider clears
    ``provider_id``/``model_id`` (``SET NULL``) instead of erasing history.
    ``meta`` is an open payload guarded by ``meta_schema_version`` (JSONB
    doctrine, constitution rule 6) and never carries prompts or completions
    (PHI).
"""

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import text

from app.models.enums import (
    AICallRoute,
    AICallStatus,
    AIPriceMatch,
    AIPriceMetering,
    AIPriceSource,
    AIUsageSource,
)

from .base import (
    Base,
    SystemScopeTenantMixin,
    TenantMixin,
    TimestampMixin,
    UserMixin,
    UUIDMixin,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


class AITaskDef(Base, TimestampMixin):
    """One AI task in the seeded vocabulary (``code`` = the registry id).

    ``id`` is deterministic (uuid5 over ``code`` — see
    :func:`app.ai.providers.task_defs.task_def_id`) so the boot seeder is
    idempotent and an audit row's FK target is derivable without a lookup.
    ``enabled`` hides a task from the settings surface without breaking rows
    that still reference it.
    """

    __tablename__ = "ai_task_defs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    label_key = Column(String(120), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))


class AICallLog(Base, UUIDMixin, SystemScopeTenantMixin, UserMixin):
    """One row per AI model call (operational audit; plan 4 W3).

    ``tenant_id`` is nullable because system-scope work (provider setup probes,
    system-level tasks) legitimately runs outside a tenant — the
    ``audit_events`` precedent. ``cost_micros`` is integer micro-units (never
    float money), resolved at write time from the tenant override, the family
    built-in catalog, loopback locality, or the pre-contract model ``settings``
    rates. ``NULL`` means the price was unknown at call time — never zero.
    """

    __tablename__ = "ai_call_logs"

    task_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_task_defs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    route = Column(
        SQLEnum(AICallRoute, values_callable=_enum_values),
        nullable=False,
    )
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    # No FK: the audit outlives the patient row (and non-clinical calls have none).
    patient_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    latency_ms = Column(Integer, nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    provider_key = Column(String(40), nullable=True)
    cost_micros = Column(BigInteger, nullable=True)
    usage_source = Column(
        SQLEnum(AIUsageSource, native_enum=False, values_callable=_enum_values, length=20),
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
    )
    estimate_method = Column(String(40), nullable=True)
    price_source = Column(
        SQLEnum(AIPriceSource, native_enum=False, values_callable=_enum_values, length=20),
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
    )
    price_version = Column(String(120), nullable=True)
    status = Column(
        SQLEnum(AICallStatus, values_callable=_enum_values),
        nullable=False,
    )
    error_class = Column(String(80), nullable=True)
    trace_id = Column(String(64), nullable=True)
    meta = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    meta_schema_version = Column(Integer, nullable=False, default=1, server_default=text("1"))

    __table_args__ = (
        CheckConstraint(
            "usage_source IN ('provider', 'estimated', 'partial', 'unknown')",
            name="usage_source_allowed",
        ),
        CheckConstraint(
            "price_source IN ('user', 'builtin', 'local', "  # gate-allow: legacy
            "'legacy', 'unknown')",  # gate-allow: legacy
            name="price_source_allowed",
        ),
        Index("ix_ai_call_logs_tenant_started", "tenant_id", "started_at"),
        Index(
            "ix_ai_call_logs_tenant_task_started",
            "tenant_id",
            "task_type_id",
            "started_at",
        ),
    )


class AIPriceOverride(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Tenant-scoped model price override (plan 58 P2).

    ``provider_key`` binds a family preset; ``provider_ref`` binds a concrete
    provider row (the honest binding for a custom provider with no preset).
    Exactly one of the two is normally set; a row with neither is a tenant
    default for the model. RLS ``TENANT_ONLY`` keeps every row inside its
    tenant.
    """

    __tablename__ = "ai_price_overrides"
    __table_args__ = (
        CheckConstraint(
            "provider_key IS NULL OR provider_ref IS NULL",
            name="one_provider_binding",
        ),
        CheckConstraint("match IN ('exact', 'prefix')", name="match_allowed"),
        CheckConstraint(
            "metering IN ('tokens', 'input_only', 'output_only')",
            name="metering_allowed",
        ),
        Index(
            "uq_ai_price_overrides_scope",
            "tenant_id",
            "provider_key",
            "provider_ref",
            "model",
            "match",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    provider_key = Column(String(40), nullable=True)
    provider_ref = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="CASCADE"),
        nullable=True,
    )
    model = Column(String(200), nullable=False)
    match = Column(
        SQLEnum(AIPriceMatch, native_enum=False, values_callable=_enum_values, length=10),
        nullable=False,
        default="exact",
        server_default=text("'exact'"),
    )
    input_micros_per_million = Column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    output_micros_per_million = Column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    metering = Column(
        SQLEnum(AIPriceMetering, native_enum=False, values_callable=_enum_values, length=20),
        nullable=False,
        default="tokens",
        server_default=text("'tokens'"),
    )
