from .base import BaseHealthProvider, BaseConfigFlow
from .observation_builder import ObservationBuilder
from .secrets import SecretCipher, encrypt_fields, decrypt_fields, mask_fields
from .auth import (
    SmartOAuth,
    OAuthTokenStore,
    OAuthStateStore,
    generate_pkce,
    generate_state,
    discover_smart,
    register_client,
    build_authorize_url,
    exchange_code,
    refresh_token,
)
from .http import http_request, paginate_bundle
from .fhir import fhir_search, fhir_observation_to_create, fhir_create, fhir_conditional_update, parse_operation_outcome
# ``ClinicalEventCreate`` is re-exported so integration providers can build
# event payloads in ``pull_clinical_events`` without reaching into
# ``app.schemas``. The platform engine resolves the ``source_integration_id``
# (workstream B.2) — providers only set ``external_id`` on the payload.
from app.schemas.clinical_event import ClinicalEventCreate
# ``ExaminationCreate`` similarly re-exported for ``pull_examinations``
# (workstream E.3).
from app.schemas.examination import ExaminationCreate
# Phase 4 of the fhir-server multi-resource sync plan: the treatment-
# resource pull hooks (supports_medications / supports_allergies /
# supports_immunizations) return these typed payloads.
from app.schemas.medication import MedicationRecordCreate
from app.schemas.allergy import AllergyIntoleranceCreate
from app.schemas.vaccine import PatientImmunizationCreate
from .catalog import (
    CatalogProposal,
    CatalogProposalKind,
    biomarker_proposal,
    medication_proposal,
    concept_proposal,
    edge_proposal,
)
from .proposals import (
    IntegrationProposalSpec,
    IntegrationProposalType,
    ProposalOutcome,
    biomarker_hitl_proposal,
    medication_hitl_proposal,
    concept_hitl_proposal,
    edge_hitl_proposal,
)
from .documents import DocumentPull
from .webhook_security import (
    verify_hmac_signature,
    verify_canonical_signature,
    verify_stripe_signature,
    get_signature_header,
    DEFAULT_WEBHOOK_SIGNATURE_HEADERS,
    STRIPE_SIGNATURE_HEADERS,
)
from .net_guard import (
    assert_safe_url,
    is_blocked_ip,
    SSRFBlockedError,
)
from .display import (
    kv_block,
    list_block,
    table_block,
    json_block,
    text_block,
    code_block,
    action_result,
)
from .notifications import (
    NotificationAction,
    NotificationSpec,
    NotificationSpecBuilder,
    NotificationTypeSpec,
)
from .exceptions import (
    IntegrationError,
    IntegrationAuthError,
    IntegrationRateLimitError,
    IntegrationDataError,
    IntegrationConfigError,
)

__all__ = [
    "BaseHealthProvider",
    "BaseConfigFlow",
    "ObservationBuilder",
    "SecretCipher",
    "encrypt_fields",
    "decrypt_fields",
    "mask_fields",
    "SmartOAuth",
    "OAuthTokenStore",
    "OAuthStateStore",
    "generate_pkce",
    "generate_state",
    "discover_smart",
    "register_client",
    "build_authorize_url",
    "exchange_code",
    "refresh_token",
    "http_request",
    "paginate_bundle",
    "fhir_search",
    "fhir_observation_to_create",
    "fhir_create",
    "fhir_conditional_update",
    "parse_operation_outcome",
    "ClinicalEventCreate",
    "ExaminationCreate",
    "MedicationRecordCreate",
    "AllergyIntoleranceCreate",
    "PatientImmunizationCreate",
    "CatalogProposal",
    "CatalogProposalKind",
    "biomarker_proposal",
    "medication_proposal",
    "concept_proposal",
    "edge_proposal",
    "IntegrationProposalSpec",
    "IntegrationProposalType",
    "ProposalOutcome",
    "biomarker_hitl_proposal",
    "medication_hitl_proposal",
    "concept_hitl_proposal",
    "edge_hitl_proposal",
    "DocumentPull",
    "verify_hmac_signature",
    "verify_canonical_signature",
    "verify_stripe_signature",
    "get_signature_header",
    "DEFAULT_WEBHOOK_SIGNATURE_HEADERS",
    "STRIPE_SIGNATURE_HEADERS",
    "assert_safe_url",
    "is_blocked_ip",
    "SSRFBlockedError",
    "kv_block",
    "list_block",
    "table_block",
    "json_block",
    "text_block",
    "code_block",
    "action_result",
    "NotificationAction",
    "NotificationSpec",
    "NotificationSpecBuilder",
    "NotificationTypeSpec",
    "IntegrationError",
    "IntegrationAuthError",
    "IntegrationRateLimitError",
    "IntegrationDataError",
    "IntegrationConfigError",
]

