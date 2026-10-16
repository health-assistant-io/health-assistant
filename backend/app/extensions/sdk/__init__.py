from app.models.enums import CodingSystem
from app.schemas.allergy import AllergyIntoleranceCreate

# ``ClinicalEventCreate`` is re-exported so integration providers can build
# event payloads in ``pull_clinical_events`` without reaching into
# ``app.schemas``. The platform engine resolves the ``source_integration_id``
# (workstream B.2) — providers only set ``external_id`` on the payload.
from app.schemas.clinical_event import ClinicalEventCreate

# ``ExaminationCreate`` similarly re-exported for ``pull_examinations``
# (workstream E.3).
from app.schemas.examination import ExaminationCreate

# ``ObservationCreate`` + ``CodingSystem`` are re-exported so providers build
# observations end-to-end without reaching into ``app.schemas`` /
# ``app.models.enums`` — parity with the other re-exported create schemas
# below. The ObservationBuilder depends on CodingSystem already.
from app.schemas.fhir.observation import ObservationCreate

# Phase 4 of the fhir-server multi-resource sync plan: the treatment-
# resource data-class hooks (medication / allergy / immunization) return
# these typed payloads.
from app.schemas.medication import MedicationRecordCreate
from app.schemas.vaccine import PatientImmunizationCreate

from .auth import (
    OAuthStateStore,
    OAuthTokenStore,
    SmartOAuth,
    build_authorize_url,
    discover_smart,
    exchange_code,
    generate_pkce,
    generate_state,
    refresh_token,
    register_client,
)
from .base import BaseConfigFlow, BaseHealthProvider
from .catalog import (
    CatalogProposal,
    CatalogProposalKind,
    biomarker_proposal,
    concept_proposal,
    edge_proposal,
    medication_proposal,
)
from .display import (
    action_result,
    code_block,
    json_block,
    kv_block,
    list_block,
    table_block,
    text_block,
)
from .documents import DocumentPull
from .exceptions import (
    BridgeError,
    IntegrationAuthError,
    IntegrationConfigError,
    IntegrationDataError,
    IntegrationError,
    IntegrationRateLimitError,
)
from .fhir import (
    fhir_conditional_update,
    fhir_create,
    fhir_observation_to_create,
    fhir_search,
    parse_operation_outcome,
)
from .http import http_request, paginate_bundle
from .net_guard import (
    SSRFBlockedError,
    assert_safe_url,
    is_blocked_ip,
)
from .notifications import (
    NotificationAction,
    NotificationSpec,
    NotificationSpecBuilder,
    NotificationTypeSpec,
)
from .observation_builder import ObservationBuilder
from .proposals import (
    IntegrationProposalSpec,
    IntegrationProposalType,
    ProposalOutcome,
    biomarker_hitl_proposal,
    concept_hitl_proposal,
    edge_hitl_proposal,
    medication_hitl_proposal,
)
from .secrets import SecretCipher, decrypt_fields, encrypt_fields, mask_fields
from .webhook_security import (
    DEFAULT_WEBHOOK_SIGNATURE_HEADERS,
    STRIPE_SIGNATURE_HEADERS,
    get_signature_header,
    verify_canonical_signature,
    verify_hmac_signature,
    verify_stripe_signature,
)

__all__ = [
    "DEFAULT_WEBHOOK_SIGNATURE_HEADERS",
    "STRIPE_SIGNATURE_HEADERS",
    "AllergyIntoleranceCreate",
    "BaseConfigFlow",
    "BaseHealthProvider",
    "BridgeError",
    "CatalogProposal",
    "CatalogProposalKind",
    "ClinicalEventCreate",
    "CodingSystem",
    "DocumentPull",
    "ExaminationCreate",
    "IntegrationAuthError",
    "IntegrationConfigError",
    "IntegrationDataError",
    "IntegrationError",
    "IntegrationProposalSpec",
    "IntegrationProposalType",
    "IntegrationRateLimitError",
    "MedicationRecordCreate",
    "NotificationAction",
    "NotificationSpec",
    "NotificationSpecBuilder",
    "NotificationTypeSpec",
    "OAuthStateStore",
    "OAuthTokenStore",
    "ObservationBuilder",
    "ObservationCreate",
    "PatientImmunizationCreate",
    "ProposalOutcome",
    "SSRFBlockedError",
    "SecretCipher",
    "SmartOAuth",
    "action_result",
    "assert_safe_url",
    "biomarker_hitl_proposal",
    "biomarker_proposal",
    "build_authorize_url",
    "code_block",
    "concept_hitl_proposal",
    "concept_proposal",
    "decrypt_fields",
    "discover_smart",
    "edge_hitl_proposal",
    "edge_proposal",
    "encrypt_fields",
    "exchange_code",
    "fhir_conditional_update",
    "fhir_create",
    "fhir_observation_to_create",
    "fhir_search",
    "generate_pkce",
    "generate_state",
    "get_signature_header",
    "http_request",
    "is_blocked_ip",
    "json_block",
    "kv_block",
    "list_block",
    "mask_fields",
    "medication_hitl_proposal",
    "medication_proposal",
    "paginate_bundle",
    "parse_operation_outcome",
    "refresh_token",
    "register_client",
    "table_block",
    "text_block",
    "verify_canonical_signature",
    "verify_hmac_signature",
    "verify_stripe_signature",
]
