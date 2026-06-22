import os
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.core.logging_setup import setup_logging
from app.api.v1 import api_router
from app.core.config import settings
from app.services.fhir_helpers import FhirSerializationError

# Configure logging
setup_logging(log_name="backend", debug=settings.DEBUG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
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
                stuck_threshold = _dt.datetime.now(
                    _dt.timezone.utc
                ) - _dt.timedelta(minutes=20)
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
            logger.error(f"Failed to cleanup stuck extractions: {e}")

    # Seed initial data
    from app.services.seed_service import seed_service
    from app.core.integration_registry import integration_registry
    from app.core.database import DATABASE_AVAILABLE

    if DATABASE_AVAILABLE:
        try:
            logger.info("Syncing medication catalog...")
            stats = await seed_service.seed_medications()
            logger.info(f"Medication sync complete: {stats}")

            logger.info("Syncing clinical event types catalog...")
            stats_events = await seed_service.seed_clinical_event_types()
            logger.info(f"Clinical event types sync complete: {stats_events}")

            logger.info("Syncing allergy catalog...")
            stats_allergies = await seed_service.seed_allergies()
            logger.info(f"Allergy sync complete: {stats_allergies}")

            logger.info("Syncing body parts catalog...")
            stats_body_parts = await seed_service.seed_body_parts()
            logger.info(f"Body parts sync complete: {stats_body_parts}")
        except Exception as e:
            logger.error(f"Failed to seed catalogs: {e}")
            
        try:
            from app.core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await integration_registry.initialize(db)
        except Exception as e:
            logger.error(f"Failed to initialize integrations: {e}")

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


@app.exception_handler(FhirSerializationError)
async def fhir_validation_handler(request: Request, exc: FhirSerializationError):
    return JSONResponse(
        status_code=400,
        content={"message": "FHIR validation failed", "detail": str(exc)},
    )


# CORS middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

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
