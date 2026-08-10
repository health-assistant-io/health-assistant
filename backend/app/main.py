import os
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.requests import ClientDisconnect
from app.core.logging_setup import setup_logging
from app.api.v1 import api_router
from app.core.config import settings
from app.catalogs.policy import CatalogConflict, CatalogPermissionDenied
from app.services.fhir_helpers import FhirSerializationError
from app.services.observation_value_validator import InvalidObservationValue
from app.core.errors import DomainError

# Configure logging
setup_logging(log_name="backend", debug=settings.DEBUG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events.

    Failure mode policy (audit item J9): in development, startup steps are
    fail-soft (log + continue) so the developer can fix the issue while the
    app keeps booting. In production, the same failures are fatal — refusing
    to boot surfaces misconfiguration early instead of running half-initialised
    (e.g. no medication/allergy catalog, no integrations). The process
    supervisor (systemd, Docker restart policy) handles restarts.
    """
    fail_soft = settings.APP_ENV == "development" or settings.DEBUG

    def _abort_or_warn(exc: Exception, what: str) -> None:
        """Log the failure; in prod, re-raise so the app refuses to boot."""
        if fail_soft:
            logger.warning("%s failed (development mode, continuing): %s", what, exc)
        else:
            logger.error("%s failed (production mode, aborting startup): %s", what, exc)
            raise exc

    # Startup
    if settings.DEBUG:
        try:
            from app.core.database import init_db

            await init_db()
        except Exception as e:
            logger.warning(f"Database initialization failed: {e}")
            logger.info(
                "Continuing without database tables. Run migrations when database is available."
            )

    # Background worker (Celery) is managed by the process supervisor
    # (systemd, Docker restart policy, etc.) — not auto-healed from the app.

    # Cleanup stuck extractions from previous runs.
    # Audit item A6: original code marked EVERY active-status exam as
    # failed on every boot — including exams being actively processed by
    # a worker at that moment (severe under rolling restarts). Now we
    # only target exams whose ``updated_at`` is older than the Celery
    # hard ``task_time_limit`` (900s) plus a safety margin (5 min) —
    # matching the periodic ``cleanup_stuck_extractions`` beat.
    from app.core.database import DATABASE_AVAILABLE
    import datetime as _dt

    if DATABASE_AVAILABLE:
        try:
            from sqlalchemy import update
            from app.models.examination_model import ExaminationModel
            from app.core.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                stuck_threshold = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
                    minutes=20
                )
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
                    .where(ExaminationModel.updated_at < stuck_threshold)
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
        except Exception as e:
            # Stuck-extraction cleanup is best-effort even in prod — a stale
            # row won't block boot, it just means a stuck exam gets cleaned
            # up by the next periodic beat instead.
            logger.error(f"Failed to cleanup stuck extractions: {e}")

    # Seed initial data
    from app.services.seed_service import seed_service
    from app.core.integration_registry import integration_registry
    from app.core.database import DATABASE_AVAILABLE

    if DATABASE_AVAILABLE:
        try:
            logger.info("Running seed stages in dependency order...")
            all_stats = await seed_service.seed_all()
            logger.info("All seed stages complete: %s", all_stats)
        except Exception as e:
            _abort_or_warn(e, "Catalog seeding")

        try:
            from app.core.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await integration_registry.initialize(db)
        except Exception as e:
            _abort_or_warn(e, "Integration registry initialization")

        # Demo mode — auto-seed the demo tenant + user + clinical data so the
        # frontend can sign in with no credentials via POST /auth/demo-login.
        # seed_demo.py is idempotent, so re-running on every boot is cheap
        # (a few existence checks). See app/core/config.py DEMO_MODE + docs.
        if settings.DEMO_MODE:
            try:
                logger.info("DEMO_MODE is on — seeding demo data...")
                from scripts.seed_demo import seed as _seed_demo

                await _seed_demo()
            except Exception as e:
                _abort_or_warn(e, "Demo data seeding")

        # First-run setup token. If the system is uninitialized (zero
        # users), prepare the setup token per SETUP_TOKEN_MODE and log
        # mode-specific guidance. The wizard reads the mode + URL hint via
        # /auth/setup-status. See app/core/setup_token.py +
        # dev/audits/setup-token-modes.md.
        try:
            from sqlalchemy import func, select

            from app.core.database import AsyncSessionLocal
            from app.core import setup_token
            from app.models.user_model import UserModel

            async with AsyncSessionLocal() as db:
                count_result = await db.execute(
                    select(func.count()).select_from(UserModel)
                )
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
        except Exception as e:
            logger.warning("Could not check first-run setup status: %s", e)

    yield
    # Shutdown
    try:
        from app.core.integration_registry import integration_registry

        for provider in integration_registry.get_all_providers():
            try:
                await provider.close()
            except Exception as e:
                logger.warning(f"Failed to close integration {provider.domain}: {e}")
        logger.info("Integrations closed.")
    except Exception as e:
        logger.warning(f"Failed to close integrations on shutdown: {e}")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="Universal Health Data Platform API - Restored",
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global 500 handler — never leaks internal exception detail to clients.

    Logs the full exception server-side with a correlation id, then returns
    a generic message + the correlation id so support can locate the entry.
    In DEBUG mode the detail is surfaced for developer convenience.
    """
    import uuid as _uuid

    correlation_id = str(_uuid.uuid4())
    logger.error(
        "GLOBAL ERROR [correlation_id=%s]: %s",
        correlation_id,
        exc,
        exc_info=True,
    )
    if settings.DEBUG:
        return JSONResponse(
            status_code=500,
            content={
                "message": "Internal server error",
                "detail": str(exc),
                "correlation_id": correlation_id,
            },
        )
    return JSONResponse(
        status_code=500,
        content={
            "message": "Internal server error",
            "detail": "An internal error occurred. Contact support with this correlation id.",
            "correlation_id": correlation_id,
        },
    )


@app.exception_handler(ClientDisconnect)
async def client_disconnect_handler(request: Request, exc: ClientDisconnect):
    """Handle clients that close the connection mid-request.

    Triggered when ``await request.body()`` / ``request.stream()`` finds the
    peer gone (mobile app backgrounded, network drop, user cancel, etc.).
    This is expected behaviour, not a server fault — log at INFO without a
    stack trace and return 499 (nginx's "Client Closed Request"). Uvicorn
    won't actually send the response (the socket is gone), but the status
    surfaces correctly in access logs / metrics and, crucially, the request
    no longer trips the generic 500 ``GLOBAL ERROR`` path with a
    ``correlation_id`` and the noisy ``Database session error:`` follow-up
    from ``get_db``'s except clause.
    """
    logger.info(
        "Client disconnected mid-request: %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(status_code=499, content={"detail": "Client disconnected"})


@app.exception_handler(FhirSerializationError)
async def fhir_validation_handler(request: Request, exc: FhirSerializationError):
    return JSONResponse(
        status_code=400,
        content={"message": "FHIR validation failed", "detail": str(exc)},
    )


@app.exception_handler(InvalidObservationValue)
async def invalid_observation_value_handler(
    request: Request, exc: InvalidObservationValue
):
    """Map the Observation↔BiomarkerDefinition contract violation to HTTP 422.

    Raised by ``validate_observation_value`` (the single chokepoint on every
    Observation write path) when the value[x] shape doesn't match the
    biomarker's ``value_type`` contract (QUANTITY vs STATE, allowed-state
    membership, multi-state component[]). 422 — the payload was syntactically
    valid FHIR but semantically inconsistent with the linked biomarker.
    """
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    """Map service-layer domain exceptions to their HTTP status (audit C1).

    ``DomainError`` subclasses (NotFoundError/AuthorizationError/ValidationError/
    ConflictError/ConcurrencyError) carry a safe, client-facing ``detail`` and a
    ``status_code``. Logged at INFO (these are expected client errors, not server
    faults) — unlike the global 500 handler, no correlation id is needed.
    """
    logger.info("Domain error [%s] %s: %s", exc.status_code, type(exc).__name__, exc.detail)
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}
    )


@app.exception_handler(CatalogPermissionDenied)
async def catalog_permission_denied_handler(
    request: Request, exc: CatalogPermissionDenied
):
    """Map the uniform catalog RBAC exception to HTTP 403.

    Both the ``/catalogs`` meta-layer adapters and the domain catalog endpoints
    (biomarkers/medications/allergies) raise ``CatalogPermissionDenied`` from
    :class:`~app.catalogs.policy.CatalogAccessPolicy.check_write``; this single
    handler turns it into a 403 without per-route try/except.
    """
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(CatalogConflict)
async def catalog_conflict_handler(request: Request, exc: CatalogConflict):
    """Map a scope-transition slug collision to HTTP 409.

    Raised by ``BaseCatalogAdapter._check_slug_collision`` when a promote would
    create a duplicate slug at the target scope tier. The body carries the
    conflicting item's id + name so the client can offer "open the existing
    item" without a re-query.
    """
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "code": "catalog_conflict",
            "slug": exc.slug,
            "target_scope": exc.target_scope,
            "existing_id": exc.existing_id,
            "existing_name": exc.existing_name,
        },
    )


# CORS middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)


# Security: baseline response headers (audit A7). Applied to every response.
# HSTS only makes sense over HTTPS and is most effective when set by the
# reverse proxy; we still emit it so direct-HTTPS deployments are protected.
# CSP is intentionally permissive for an API+SPA (the frontend is a separate
# origin); tighten APP_CSP_CONTENT via env if you serve the SPA from here.
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # X-Frame-Options: DENY is the safe baseline. Routes that legitimately
    # need to be embedded in a same-origin <iframe> (e.g. the inline document
    # download used for PDF preview) may set the header themselves; use
    # setdefault so an explicit per-route value wins. See documents.py.
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Security: CORS configuration
if settings.APP_ENV == "development":
    # In development, allow any local network origin (LAN) via regex
    # Matches localhost, 127.0.0.1, and private IP ranges (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Pages", "X-Current-Page", "X-Total-Frames"],
    )
else:
    # In production, restrict to specific trusted domains. Hostnames must be
    # RFC 1123 compliant (no underscores); FRONTEND_URL env is the source of truth.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("FRONTEND_URL", "https://app.health-assistant.com")],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["X-Total-Pages", "X-Current-Page", "X-Total-Frames"],
    )

# Include routers
app.include_router(api_router)


@app.get("/.well-known/smart-configuration", tags=["oauth"])
async def smart_configuration():
    """SMART-on-FHIR discovery document for the FHIR facade."""
    from app.services.fhir_facade_service import build_smart_configuration

    return build_smart_configuration()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    from app.core.database import DATABASE_AVAILABLE

    db_status = "connected" if DATABASE_AVAILABLE else "not_available"

    return {"status": "healthy", "database": db_status, "redis": "not_configured"}


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": settings.APP_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
