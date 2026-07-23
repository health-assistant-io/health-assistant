from fastapi import APIRouter, Depends
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.tenants import router as tenants_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.fhir_r4 import router as fhir_r4_router
from app.api.v1.endpoints.patients import router as patients_router
from app.api.v1.endpoints.observations import router as observations_router
from app.api.v1.endpoints.telemetry import router as telemetry_router
from app.api.v1.endpoints.notifications import router as notifications_router
from app.api.v1.endpoints.notification_rules import router as notification_rules_router
from app.api.v1.endpoints.analytics import router as analytics_router
from app.api.v1.endpoints.import_data import router as import_router
from app.api.v1.endpoints.export import router as export_router
from app.api.v1.endpoints.examinations import router as examinations_router
from app.api.v1.endpoints.patient_layout import router as patient_layout_router
from app.api.v1.endpoints.doctors import router as doctors_router
from app.api.v1.endpoints.allergies import router as allergies_router
from app.api.v1.endpoints.medications import router as medications_router
from app.api.v1.endpoints.vaccines import router as vaccines_router
from app.api.v1.endpoints.biomarkers import router as biomarkers_router
from app.api.v1.endpoints.ai_config import router as ai_config_router
from app.api.v1.endpoints.task_monitor import router as task_monitor_router
from app.api.v1.endpoints.ai_assistance import router as ai_assistance_router
from app.api.v1.endpoints.clinical_events import router as clinical_events_router
from app.api.v1.endpoints.anatomy import router as anatomy_router
from app.api.v1.endpoints.organizations import router as organizations_router
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.admin_tenants import router as admin_tenants_router
from app.api.v1.endpoints.integrations import router as integrations_router
from app.api.v1.endpoints.admin_integrations import router as admin_integrations_router
from app.api.v1.endpoints.search import router as search_router
from app.api.v1.endpoints.instances import router as instances_router
from app.api.v1.endpoints.concepts import (
    router as concepts_router,
    edge_router as concept_edges_router,
)
from app.api.v1.endpoints.catalogs import router as catalogs_router
from app.api.v1.endpoints.settings import router as settings_router
from app.api.v1.endpoints.websockets import router as websockets_router
from app.api.v1.endpoints.oauth import router as oauth_router
from app.core.security import require_session_token

api_router = APIRouter(prefix="/api/v1")

# --- External surface: accepts OAuth2 api tokens (NOT session-gated) -------
# The FHIR R4 facade is the public interop surface; OAuth endpoints mint the
# api tokens that authenticate against it. Session-only guard does not apply.
api_router.include_router(oauth_router)
api_router.include_router(fhir_r4_router)

# --- Internal surface: session tokens only (frontend / mobile) -------------
# The guard rejects ``token_kind="api"`` tokens on every domain route so an
# OAuth client can never reach the ORM-shape REST API. Anonymous requests and
# session JWTs pass through; each endpoint's own ``get_current_user`` still
# enforces authentication where required. WebSocket auth is separate
# (Sec-WebSocket-Protocol), so the WS router sits outside the guard.
session_gated_router = APIRouter(dependencies=[Depends(require_session_token)])

session_gated_router.include_router(auth_router)
session_gated_router.include_router(tenants_router)
session_gated_router.include_router(users_router)
session_gated_router.include_router(documents_router)
session_gated_router.include_router(examinations_router)
session_gated_router.include_router(patients_router)
session_gated_router.include_router(observations_router)
session_gated_router.include_router(telemetry_router)
session_gated_router.include_router(notifications_router)
session_gated_router.include_router(notification_rules_router)
session_gated_router.include_router(analytics_router)
session_gated_router.include_router(import_router)
session_gated_router.include_router(export_router)
session_gated_router.include_router(patient_layout_router)
session_gated_router.include_router(doctors_router)
session_gated_router.include_router(organizations_router)
session_gated_router.include_router(allergies_router)
session_gated_router.include_router(medications_router)
session_gated_router.include_router(vaccines_router)
session_gated_router.include_router(biomarkers_router)
session_gated_router.include_router(ai_config_router)
session_gated_router.include_router(task_monitor_router)
session_gated_router.include_router(ai_assistance_router)
session_gated_router.include_router(clinical_events_router)
session_gated_router.include_router(anatomy_router, prefix="/anatomy", tags=["Anatomy Graph"])
session_gated_router.include_router(admin_router)
session_gated_router.include_router(admin_tenants_router)
session_gated_router.include_router(
    integrations_router, prefix="/integrations", tags=["Integrations"]
)
session_gated_router.include_router(
    admin_integrations_router, prefix="/admin/integrations", tags=["Admin Integrations"]
)
session_gated_router.include_router(search_router, prefix="/search", tags=["Search"])
session_gated_router.include_router(instances_router)
session_gated_router.include_router(concepts_router)
session_gated_router.include_router(concept_edges_router)
session_gated_router.include_router(catalogs_router)
session_gated_router.include_router(settings_router)

api_router.include_router(session_gated_router)
api_router.include_router(websockets_router)
