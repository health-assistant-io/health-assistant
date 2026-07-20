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
]

