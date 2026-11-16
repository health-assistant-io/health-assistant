"""Boot-time database operations under explicit RLS scopes (ADR 0054 D4).

The application lifespan's database work runs **before any request**, so no
principal context exists: the contextvars sit at their closed default, and
under row-level security a closed context answers zero rows. Unwrapped, the
boot sequence broke at head: the first-run setup-token count always read 0
(every boot re-announced setup, and the stale-token clear never ran), the
stuck-examination cleanup matched nothing, the bundled seed apply could not
read its NULL-tenant (system) source rows or write new ones, and the demo
seeder could not seed its tenant.

Every step here therefore plants an explicit
``tenant_context.system_scope("boot:<step>")`` around its database work and
restores the (closed) ambient context on exit — the same "no ambient
system" rule as every other context surface, applied at boot. The functions
below are the *only* way ``main.lifespan`` touches the database, and the
RLS boot test (``tests/test_rls_boot.py``) runs them against a scratch
database **as the runtime role** (``neuronection_health_app``), so a boot
query added without its scope fails there instead of in production.

Instance-fact reads that touch only tenant-less tables
(``instance_settings`` — ``demo_mode_enabled``) need no scope; everything
tenant-scoped or NULL-tenant-scoped does.
"""

from __future__ import annotations

import logging
from typing import cast

from sqlalchemy.sql.elements import ColumnElement

logger = logging.getLogger(__name__)


async def initialize_instance_state() -> None:
    """Consume ``HA_AUTH_MODE``/``HA_DEMO_MODE`` once on an empty DB (§4)."""
    from app.core import instance_state, tenant_context

    with tenant_context.system_scope("boot:instance-state"):
        await instance_state.initialize()


async def cleanup_stuck_examinations() -> int:
    """Fail examinations stuck in an active status past the task limit.

    Audit item A6: only rows older than the Celery hard ``task_time_limit``
    (900 s) plus a safety margin are touched — never exams a worker is
    actively processing. Returns the cleaned-up count (best-effort even in
    prod; the caller logs, never aborts, on failure).
    """
    import datetime as _dt

    from sqlalchemy import update

    from app.core import tenant_context
    from app.core.database import AsyncSessionLocal
    from app.models.examination_model import ExaminationModel

    with tenant_context.system_scope("boot:stuck-exam-cleanup"):
        async with AsyncSessionLocal() as db:
            stuck_threshold = _dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=20)
            # Target statuses that indicate the process is active.
            stuck_statuses = [
                "aggregating",
                "analyzing_text",
                "defining_ontology",
                "persisting_results",
                "processing",
            ]
            result = await db.execute(
                update(ExaminationModel)
                .where(ExaminationModel.extraction_status.in_(stuck_statuses))
                # Column attributes assigned in a (non-declarative) mixin class
                # body resolve to `Never` under the mypy 2.3 x SQLAlchemy 2.1
                # inline types, so the `<` comparison falls back to `bool`.
                # The cast re-states the comparison's real type; runtime is
                # untouched (cast is a no-op).
                .where(cast("ColumnElement[bool]", ExaminationModel.updated_at < stuck_threshold))
                .values(
                    extraction_status="failed",
                    extraction_progress=0,
                    error_message="Task timeout (startup cleanup)",
                )
            )
            count = result.rowcount
            if count > 0:
                await db.commit()
                logger.info(
                    "Cleaned up %d stuck examinations (older than %s) from previous session.",
                    count,
                    stuck_threshold.isoformat(),
                )
            return count


async def apply_bundled_seed_pack() -> dict | None:
    """Apply the bundled seed pack when its digest changed (ADR 0056).

    The bundled pack is the trusted source: boot applies it only when the
    digest changed (or nothing applied it yet). The applier owns its
    per-stage transactions and writes provenance + run history + audit.
    Returns the outcome stats, or ``None`` when the digest is unchanged.
    """
    from app.core import tenant_context
    from app.services.seed.bundled_source import apply_bundled_on_boot

    with tenant_context.system_scope("boot:seed-apply"):
        return await apply_bundled_on_boot()


async def initialize_integration_registry() -> None:
    """Load the instance's integration providers (one session, no request)."""
    from app.core import tenant_context
    from app.core.database import AsyncSessionLocal
    from app.extensions.registry import integration_registry

    with tenant_context.system_scope("boot:integration-registry"):
        async with AsyncSessionLocal() as db:
            await integration_registry.initialize(db)


async def seed_demo_data(database_url: str | None = None) -> None:
    """Seed the demo tenant + user + clinical data (§13 guards apply).

    Called only when ``instance_settings.demo_mode`` is true — the seeder
    itself re-checks both §13 guards (target database named ``*_demo`` +
    instance ``demo_mode=true``) and raises :class:`~scripts.seed_demo.Refusal`
    otherwise. Idempotent, so re-running on every boot is cheap.
    """
    from app.core import tenant_context
    from scripts.seed_demo import seed as _seed_demo

    with tenant_context.system_scope("boot:demo-seed"):
        await _seed_demo(database_url=database_url)


async def prepare_first_run_setup_token() -> None:
    """Prepare (or retire) the first-run setup token per its mode.

    If the system is uninitialized (**zero users** — a count that must see
    every tenant under RLS, hence the system scope), prepare the token per
    ``SETUP_TOKEN_MODE`` and log mode-specific guidance; otherwise ensure
    no stale token lingers. The wizard reads the mode + URL hint via
    ``/auth/setup-status`` (app/core/setup_token.py + the family audit
    notes/setup-token-modes.md).
    """
    from sqlalchemy import func, select

    from app.core import setup_token, tenant_context
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    from app.models.user_model import UserModel

    with tenant_context.system_scope("boot:setup-token"):
        async with AsyncSessionLocal() as db:
            count_result = await db.execute(select(func.count()).select_from(UserModel))
            user_count = count_result.scalar() or 0

        if user_count == 0:
            mode = setup_token.current_mode()
            if mode == "env":
                if setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN):
                    logger.info(
                        "\n══════════════════════════════════════════════════════\n"
                        " FIRST-RUN SETUP REQUIRED (token mode: env)\n"
                        " Open the launcher URL supplied by your installer — the\n"
                        " setup token is injected via ?token= and the wizard\n"
                        " auto-fills it. No log-grep needed.\n"
                        "══════════════════════════════════════════════════════"
                    )
                else:
                    # config.py already downgraded to 'log' with a warning,
                    # but be defensive in case the env var was unset after boot.
                    logger.warning(
                        "SETUP_TOKEN_MODE=env but SETUP_BOOTSTRAP_TOKEN is empty — "
                        "falling back to 'log' mode for this boot."
                    )
                    token = setup_token.generate()
                    logger.info(
                        "\n══════════════════════════════════════════════════════\n"
                        " FIRST-RUN SETUP REQUIRED\n"
                        " Setup token (required if accessing remotely):\n"
                        "   %s\n"
                        " Retrieve later: docker compose ... logs backend"
                        " | grep -i -A 1 'setup token'\n"
                        " Localhost / dev access does not need the token.\n"
                        "══════════════════════════════════════════════════════",
                        token,
                    )
            elif mode == "time":
                setup_token.mark_boot_time()
                logger.info(
                    "\n══════════════════════════════════════════════════════\n"
                    " FIRST-RUN SETUP REQUIRED (token mode: time)\n"
                    " The setup wizard is tokenless for the first %d minute(s).\n"
                    " Complete first-run setup within that window, or a one-time\n"
                    " token will be minted and logged after it expires.\n"
                    " Localhost / dev access never needs the token.\n"
                    "══════════════════════════════════════════════════════",
                    settings.SETUP_TOKEN_GRACE_MINUTES,
                )
            elif mode == "disabled":
                logger.warning(
                    "\n══════════════════════════════════════════════════════\n"
                    " FIRST-RUN SETUP REQUIRED (token mode: disabled)\n"
                    " WARNING: SETUP_TOKEN_MODE=disabled skips the first-claim\n"
                    " guard entirely. Anyone who reaches /setup before the\n"
                    " operator can claim this instance. Only safe when the\n"
                    " deployment is firewalled / VPN-gated / bound to 127.0.0.1.\n"
                    "══════════════════════════════════════════════════════"
                )
            else:  # "log"
                token = setup_token.generate()
                logger.info(
                    "\n══════════════════════════════════════════════════════\n"
                    " FIRST-RUN SETUP REQUIRED\n"
                    " Open the app in your browser and complete the setup\n"
                    " wizard to create your administrator account.\n"
                    " Setup token (required if accessing remotely):\n"
                    "   %s\n"
                    " Retrieve later: docker compose ... logs backend"
                    " | grep -i -A 1 'setup token'\n"
                    " Localhost / dev access does not need the token.\n"
                    "══════════════════════════════════════════════════════",
                    token,
                )
        else:
            # Already initialized — ensure no stale token lingers.
            setup_token.clear()
