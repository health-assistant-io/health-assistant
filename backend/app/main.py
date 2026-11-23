import logging
from contextlib import asynccontextmanager

import anyio
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from nx_auth.headers import HeaderPolicy, SecurityHeadersMiddleware
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from starlette.requests import ClientDisconnect

from app.api.readiness import router as readiness_router
from app.api.v1 import api_router
from app.catalogs.policy import (
    CatalogConflict,
    CatalogPermissionDenied,
    CatalogReferenceError,
    CatalogUniqueViolation,
)
from app.core import boot
from app.core.authz.enforce import enforce_route_policy
from app.core.config import DEV_LAN_ORIGIN_REGEX, settings
from app.core.cookies import CsrfMiddleware, LegacyCookieClearingMiddleware
from app.core.csp import DOCUMENT_CSP
from app.core.errors import DomainError
from app.core.logging_setup import setup_logging
from app.services.biomarkers.observation_value_validator import InvalidObservationValue
from app.services.fhir.fhir_helpers import FhirSerializationError

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
    fail_soft = settings.HA_APP_ENV == "development" or settings.DEBUG

    def _abort_or_warn(exc: Exception, what: str) -> None:
        """Log the failure; in prod, re-raise so the app refuses to boot."""
        if fail_soft:
            logger.warning("%s failed (development mode, continuing): %s", what, exc)
        else:
            logger.error("%s failed (production mode, aborting startup): %s", what, exc)
            raise exc

    # Object storage is configuration, not a runtime fallback: resolve it
    # once, probe it, and refuse to serve on a bad bucket/container/credential
    # (ADR-0057). This runs before every other boot step so no handler can
    # ever see a half-initialised storage seam.
    from app.storage.bootstrap import open_configured_store, register
    from app.storage.documents import DocumentStorage
    from app.storage.health import StorageHealthProbe

    storage_config, objects = open_configured_store(settings)
    register(storage_config, objects)
    app.state.storage_config = storage_config
    app.state.objects = objects
    app.state.storage_health = StorageHealthProbe(storage_config, objects)
    app.state.document_storage = DocumentStorage(objects)

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

    # Every database-touching boot step runs inside an explicit
    # ``tenant_context.system_scope("boot:<step>")`` (app.core.boot — ADR
    # 0054 D4): boot time has no request context, and under row-level
    # security a closed context answers zero rows. The functions are the
    # only way the lifespan touches the database and are exercised as the
    # runtime role by tests/test_rls_boot.py.
    from app.core import tenant_context
    from app.core.database import DATABASE_AVAILABLE

    if DATABASE_AVAILABLE:
        # Stuck-extraction cleanup is best-effort even in prod — a stale
        # row won't block boot, it just means a stuck exam gets cleaned
        # up by the next periodic beat instead.
        try:
            await boot.cleanup_stuck_examinations()
        except Exception as e:
            logger.error(f"Failed to cleanup stuck extractions: {e}")

        # Instance facts (identity-auth §4): init-only — the empty DB
        # consumes HA_AUTH_MODE / HA_DEMO_MODE exactly once (here and at
        # /auth/setup); post-init env flips are ignored with a loud warning.
        try:
            await boot.initialize_instance_state()
        except Exception as e:
            _abort_or_warn(e, "Instance settings initialization")

        try:
            logger.info("Running bundled seed pack apply (digest-gated, ADR 0056)...")
            boot_stats = await boot.apply_bundled_seed_pack()
            if boot_stats is None:
                logger.info("Bundled seed pack digest unchanged — no boot apply needed.")
            else:
                logger.info("Bundled seed pack applied: %s", boot_stats)
        except Exception as e:
            _abort_or_warn(e, "Catalog seeding")

        try:
            await boot.initialize_integration_registry()
        except Exception as e:
            _abort_or_warn(e, "Integration registry initialization")

        # Demo mode — auto-seed the demo tenant + user + clinical data so the
        # frontend can sign in with no credentials via POST /auth/demo-login.
        # seed_demo.py is idempotent, so re-running on every boot is cheap
        # (a few existence checks). Gated on the **DB fact** (§4/§13): the
        # env is init-only, so a post-init env flip can never seed demo data
        # into a non-demo instance. The seeder itself re-checks both §13
        # guards (target database named *_demo + instance demo_mode=true)
        # and raises Refusal otherwise — production aborts, dev warns. See
        # app/core/config.py + docs.
        from app.core import instance_state

        if await instance_state.demo_mode_enabled():
            try:
                logger.info("demo_mode is on — seeding demo data...")
                await boot.seed_demo_data()
            except Exception as e:
                _abort_or_warn(e, "Demo data seeding")

        # First-run setup token (zero-user count + mode banners — see
        # boot.prepare_first_run_setup_token).
        try:
            await boot.prepare_first_run_setup_token()
        except Exception as e:
            logger.warning("Could not check first-run setup status: %s", e)

    # LangGraph checkpoint store (Phase 3.1): one pool per process, held for
    # the app lifetime; boot-time setup() is idempotent (spike ruling b).
    # Drained + closed on shutdown so trailing checkpoint writes survive.
    checkpoint_store = None
    if DATABASE_AVAILABLE:
        from app.ai.graphs.checkpointer import CheckpointStore, bind_runtime_store

        checkpoint_store = CheckpointStore()
        try:
            # The checkpoint tables carry no tenant columns (no policies),
            # but the scope keeps the boot invariant: no DB work without an
            # explicit context.
            with tenant_context.system_scope("boot:checkpoint-store"):
                await checkpoint_store.open()
        except Exception as e:
            _abort_or_warn(e, "Checkpoint store initialization")
        bind_runtime_store(checkpoint_store)
        app.state.checkpoint_store = checkpoint_store

    # Boot completed — /ready turns 200 (plan 64 §1.4). Hard checks still
    # gate: the DB must be reachable at the release's Alembic head and
    # required object storage must answer.
    app.state.boot_complete = True

    yield
    # Shutdown
    try:
        from app.storage.bootstrap import reset_current_store

        reset_current_store()
        logger.info("Object storage closed.")
    except Exception as e:
        logger.warning(f"Failed to close object storage cleanly: {e}")

    if checkpoint_store is not None:
        try:
            from app.ai.graphs.checkpointer import bind_runtime_store

            bind_runtime_store(None)
            await checkpoint_store.close()
        except Exception as e:
            logger.warning(f"Failed to close checkpoint store cleanly: {e}")

    try:
        from app.extensions.registry import integration_registry

        for provider in integration_registry.get_all_providers():
            try:
                await provider.close()
            except Exception as e:
                logger.warning(f"Failed to close integration {provider.domain}: {e}")
        logger.info("Integrations closed.")
    except Exception as e:
        logger.warning(f"Failed to close integrations on shutdown: {e}")


# Create FastAPI app. API docs (audit 2026-08 API-L1) are exposed only in
# dev/test or when the operator explicitly sets ENABLE_API_DOCS=true — the
# full authenticated-endpoint surface should not be advertised on an
# internet-exposed instance.
_docs_enabled = settings.ENABLE_API_DOCS or settings.HA_APP_ENV in (
    "development",
    "test",
    "testing",
)
app = FastAPI(
    title=settings.APP_NAME,
    description="Universal Health Data Platform API - Restored",
    version=settings.VERSION,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    lifespan=lifespan,
    # ADR 0054 1b — the central authorization gate runs before every HTTP
    # route's own dependencies (session 401/403, token/public classes skip,
    # unresolved routes fail closed). WebSocket handshakes do not solve
    # Request parameters; the two WS handlers run the same policy check.
    dependencies=[Depends(enforce_route_policy)],
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
async def invalid_observation_value_handler(request: Request, exc: InvalidObservationValue):
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
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(CatalogPermissionDenied)
async def catalog_permission_denied_handler(request: Request, exc: CatalogPermissionDenied):
    """Map the uniform catalog RBAC exception to HTTP 403.

    Both the ``/catalogs`` meta-layer adapters and the domain catalog endpoints
    (biomarkers/medications/allergies) raise ``CatalogPermissionDenied`` from
    :class:`~app.catalogs.policy.CatalogAccessPolicy.check_write``; this single
    handler turns it into a 403 without per-route try/except.
    """
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(CatalogReferenceError)
async def catalog_reference_error_handler(request: Request, exc: CatalogReferenceError):
    """Map a bad client reference (missing/invisible id or unresolvable slug)
    to HTTP 400 — one handler for every door.

    Raised by the unified write services when an explicit ``class_concept_id``,
    concept ``parent_id`` or class slug points at a row the caller's tenant
    cannot see (ADR 0055 Phase-3 hardening). Domain routes without their own
    ``ValueError``→400 mapping (medication/allergy/vaccine/anatomy catalog
    routes) stay typed here too; the message never carries the hidden row's
    name or slug.
    """
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(CatalogUniqueViolation)
async def catalog_unique_violation_handler(request: Request, exc: CatalogUniqueViolation):
    """Map a catalog uniqueness violation to HTTP 409 (one write contract).

    Raised by the unified catalog write services when a create/update would
    duplicate a natural/display key — ``seed_key``, ``(coding_system, code)``
    or a per-scope slug — either via a pre-check or a translated
    ``IntegrityError`` (rolled back first). The body carries the conflicting
    field so the client can highlight it.
    """
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "code": "catalog_conflict",
            "field": exc.field,
        },
    )


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


class SSEPassthroughGZipMiddleware(GZipMiddleware):
    """GZip compression that never touches ``text/event-stream`` responses.

    Compressing event streams buys nothing — every frame is flushed
    immediately — and risks buffering bubbles through intermediaries (a
    gzip encoder that only completes the stream when the stream ends).
    The exclusion is pinned explicitly here instead of being left to the
    middleware library's default exclusion list, so an upstream default
    change can never silently re-introduce SSE compression; every other
    response compresses exactly as :class:`GZipMiddleware` would.
    """

    def __init__(self, app, minimum_size: int = 500, compresslevel: int = 9):
        super().__init__(
            app,
            minimum_size=minimum_size,
            compresslevel=compresslevel,
            exclude_content_types=tuple(
                dict.fromkeys((*DEFAULT_EXCLUDED_CONTENT_TYPES, "text/event-stream"))
            ),
        )


app.add_middleware(SSEPassthroughGZipMiddleware, minimum_size=1000)

# §10 double-submit CSRF (plan 16 H3): non-safe /api/* requests that carry
# session/CSRF cookies must echo ha_csrf in X-CSRF-Token (403 otherwise).
# Mounted innermost (first-added) so its 403s flow back out through the
# security-headers + CORS layers and stay readable by the browser.
# Bearer-authenticated requests (§9 user clients) and the auth bootstrap
# endpoints are exempt — see app/core/cookies.py.
app.add_middleware(CsrfMiddleware)
# Retire pre-prefix nx_* cookies on sight (family co-location rename).
app.add_middleware(LegacyCookieClearingMiddleware)


# Security: baseline response headers (audit A7) — the kit's middleware
# with health's policy (plan 21). The CSP body lives in
# ``app.core.csp.DOCUMENT_CSP``: the SPA document is served by the
# frontend image (serve.json) / vite dev server, and the API emits the
# same directive set on every response. ``X-Frame-Options`` stays
# overridable per route (the inline document download relaxes to
# SAMEORIGIN for the PDF preview iframe — ``frame-ancestors 'self'`` in
# the CSP body matches that relaxation), and HSTS is emitted only when the
# client's connection actually arrived over TLS. Applied to every
# response; the nginx layer (ADR-0005) stays the outer connection/TLS
# layer.
app.add_middleware(
    SecurityHeadersMiddleware,
    policy=HeaderPolicy(web_csp=DOCUMENT_CSP, frame_overridable=True, hsts_behind_proxy=True),
)


# Security: CORS configuration
# Cross-origin browser clients may read these response headers
# (``Access-Control-Expose-Headers``). Beyond the pagination set, the
# audit export reports stream truncation through
# ``X-Audit-Export-Truncated``/``X-Audit-Export-Total`` (Phase 8) — a
# viewer SPA served from another origin cannot read them unless they are
# exposed, so both policies below share this list (pinned by tests).
_CORS_EXPOSE_HEADERS = [
    "X-Total-Pages",
    "X-Current-Page",
    "X-Total-Frames",
    "X-Authz-Denied",
    "X-Audit-Export-Truncated",
    "X-Audit-Export-Total",
]
if settings.HA_APP_ENV == "development":
    # In development, allow any local network origin (LAN) via regex
    # (shared with the §10 WS Origin gate — app.core.config).
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=DEV_LAN_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=_CORS_EXPOSE_HEADERS,
    )
else:
    # In production, restrict to specific trusted domains. Hostnames must be
    # RFC 1123 compliant (no underscores); FRONTEND_URL env is the source of truth.
    app.add_middleware(
        CORSMiddleware,
        # Audit 2026-08 CFG-L2: read FRONTEND_URL through pydantic settings
        # (picks up .env files consistently) instead of a raw os.getenv that
        # silently ignored .env-only configuration.
        allow_origins=[settings.FRONTEND_URL],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        # §10: X-CSRF-Token is the double-submit echo (browsers); the §9
        # Bearer clients use Authorization as before.
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
        expose_headers=_CORS_EXPOSE_HEADERS,
    )

# Audit 2026-08 CFG-L1/INT-M4: Host-header validation. The integrations
# OAuth flow builds redirect URIs from the request Host by fallback —
# pinning the app to its configured host(s) blocks Host-header poisoning
# and DCR registration attacks. Dev allows any host (LAN access).
if settings.HA_APP_ENV in ("development", "test", "testing"):
    _trusted_hosts = ["*"]
else:
    _trusted_hosts = []
    for _u in (settings.APP_URL, settings.FRONTEND_URL):
        try:
            from urllib.parse import urlparse

            _h = urlparse(_u).hostname
            if _h and _h not in _trusted_hosts:
                _trusted_hosts.append(_h)
        except Exception:
            pass
    if not _trusted_hosts:
        # Fail closed: silently allowing "*" disables Host-header poisoning
        # protection exactly where it matters (production behind a proxy).
        raise RuntimeError(
            "APP_URL/FRONTEND_URL must be valid absolute URLs in production "
            "so the Host-header allowlist can be derived."
        )
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)

# Include routers
app.include_router(api_router)
# /ready is a root-level ingress probe (plan 64 §1.4) — outside the
# versioned API on purpose (no /api prefix; the session/CSRF middleware and
# the authz route gate only ever touch /api/* paths, so the probe needs no
# exempt carve-out). The route policy pins it PUBLIC (route_policy.py).
app.include_router(readiness_router)
# Readiness flips green only when the lifespan startup below completed.
app.state.boot_complete = False


@app.get("/.well-known/smart-configuration", tags=["oauth"])
async def smart_configuration():
    """SMART-on-FHIR discovery document for the FHIR facade."""
    from app.services.fhir.fhir_facade_service import build_smart_configuration

    return build_smart_configuration()


@app.get("/health")
async def health_check(response: Response):
    """Health check endpoint (liveness — 200 unless required storage fails;
    dependency states ride the payload, never the status code: a degraded
    Redis must not fail the probe containers act on).

    Object storage is the exception to "always 200": a transient provider
    failure degrades open (``storage.status: degraded``, 200), while a
    required deployment (cloud profile or ``OBJECT_STORAGE_REQUIRED=true``)
    or a sustained failure returns 503 so the container can be restarted.
    """
    from app.core.database import DATABASE_AVAILABLE
    from app.core.redis import redis_client
    from app.storage.health import STATUS_DEGRADED

    db_status = "connected" if DATABASE_AVAILABLE else "not_available"
    try:
        redis_status = "connected" if await redis_client.ping() else "not_available"
    except Exception:
        redis_status = "not_available"

    probe = getattr(app.state, "storage_health", None)
    if probe is None:
        storage = {
            "backend": getattr(getattr(app.state, "storage_config", None), "backend", "unknown"),
            "status": "unknown",
            "required": False,
        }
    else:
        snapshot = await anyio.to_thread.run_sync(probe.snapshot)
        storage = snapshot.payload()
        if snapshot.failed:
            response.status_code = 503

    status_label = "healthy"
    if storage["status"] == STATUS_DEGRADED:
        status_label = "degraded"
    elif storage["status"] == "unavailable":
        status_label = "unhealthy"
    return {
        "status": status_label,
        "database": db_status,
        "redis": redis_status,
        "storage": storage,
    }


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
