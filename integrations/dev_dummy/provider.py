"""Dev Dummy reference provider — demonstrates EVERY SDK capability.

Each method below is annotated with the SDK hook it demonstrates and the
behaviour the platform engine expects. Toggles in ``config_flow.py`` let
you enable/disable each capability in isolation so you can see exactly
what the engine does for each one.

Capabilities demonstrated (search for the §N markers in this file):

§A  ``pull_data`` — abstract pull → ``List[ObservationCreate]``
§B  Exception mapping — ``IntegrationAuthError`` / ``IntegrationRateLimitError``
§C  Cursor-based delta sync — ``get_sync_cursor`` / ``set_sync_cursor``
§D  Debug logging — ``log_debug_payload`` (no-op unless debug enabled)
§E  Quantitative + categorical observations — ``set_value`` / ``set_value_string``
§F  Custom UI actions — ``get_custom_actions`` / ``execute_custom_action``
§G  Notifications (rich, actionable) — ``supports_notifications`` +
    ``get_notification_types`` + ``get_notifications`` +
    ``handle_notification_action``
§H  Webhook ingest — ``handle_webhook`` with HMAC validation
§I  Two-way API proxy — ``handle_api_request`` for headless clients
§J  Chat tools — ``supports_tools`` / ``get_tools`` (LangChain)
§K  Clinical events — ``supports_clinical_events`` / ``pull_clinical_events``
§L  Examinations — ``supports_examinations`` / ``pull_examinations``
§M  Catalog proposals (auto-apply) — ``supports_catalog_proposals``
§N  HITL proposals (human review) — ``supports_hitl_proposals`` +
    ``handle_proposal_resolution``
§O  Documents — ``supports_documents`` / ``pull_documents``
§P  Lifecycle — ``push_data`` / ``close`` / ``revoke``
"""
from __future__ import annotations

import random
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from integrations.sdk import (
    BaseHealthProvider,
    CatalogProposal,
    ClinicalEventCreate,
    DocumentPull,
    ExaminationCreate,
    IntegrationProposalSpec,
    NotificationSpec,
    NotificationTypeSpec,
    ProposalOutcome,
    biomarker_proposal,
    concept_hitl_proposal,
    get_signature_header,
    verify_hmac_signature,
)
from integrations.sdk.display import kv_block, list_block, table_block, action_result
from integrations.sdk.exceptions import (
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationRateLimitError,
)
from app.models.enums import ClinicalEventStatus, CodingSystem
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration


# ---------------------------------------------------------------------------
# Static notification-type declarations.
#
# Each (domain, id) pair is a user-toggleable preference stored at
# ``user.settings["notifications.integration.dev_dummy.{id}"]``. Default
# True for everything — users opt OUT, not IN. Listed in the
# IntegrationDetail "Notifications" tab + the central /settings/notifications
# rollup. Specs emitted in get_notifications() carry the matching type_id
# so the platform can filter muted kinds.
# ---------------------------------------------------------------------------
_NOTIFICATION_TYPES = [
    NotificationTypeSpec(
        id="elevated_heart_rate",
        label="Elevated heart-rate alerts",
        description="Fires when a synced heart-rate reading exceeds 100 bpm.",
        category="alert",
        severity="warning",
        default_enabled=True,
    ),
    NotificationTypeSpec(
        id="elevated_bp_review",
        label="Blood-pressure review prompts",
        description="Surfaces a HITL-style prompt when systolic >= 130 or diastolic >= 85.",
        category="hitl",
        severity="warning",
        default_enabled=True,
    ),
    NotificationTypeSpec(
        id="daily_summary",
        label="Per-sync summary",
        description="Informational summary of every measurement imported in this sync (no actions, just a table).",
        category="system",
        severity="info",
        default_enabled=True,
    ),
    NotificationTypeSpec(
        id="sensor_malfunction",
        label="Implausible-value flags",
        description="Critical alert when a reading is outside plausible range (e.g. HR > 200) — likely a sensor fault.",
        category="agent",
        severity="critical",
        default_enabled=True,
    ),
]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class DevDummyProvider(BaseHealthProvider):
    """Reference provider exercising every BaseHealthProvider hook."""

    domain = "dev_dummy"

    # ------------------------------------------------------------------
    # §setup — one-time lifecycle hook (item 5 of integrations-sdk-
    # improvements plan). The registry calls this on app startup with
    # the SystemIntegration.global_config JSONB so providers can do
    # one-time resource setup keyed off system-level config.
    # ------------------------------------------------------------------

    async def setup(self, config: dict | None = None) -> None:
        """Demonstrate the ``setup(config)`` lifecycle hook.

        The registry calls this once on app startup after instantiating
        the provider. ``config`` is the ``SystemIntegration.global_config``
        JSONB (when present). Use it for one-time resource setup — HTTP
        pools, OAuth discovery, signing keys, entitlement checks, etc.

        Failures bubble up to the registry's loader, which logs them
        and excludes the integration from the active set (so a buggy
        provider can't crash the whole startup loop).
        """
        await super().setup(config or {})
        self.logger.info(
            "dev_dummy setup() called with system_config=%s",
            config,
        )

    # ==================================================================
    # §F — Custom UI actions
    # ==================================================================

    def get_custom_actions(self) -> List[Dict[str, str]]:
        """Buttons shown on the integration detail page.

        ``style`` is one of ``primary`` / ``danger`` / ``warning`` /
        ``default`` (the frontend maps these to Tailwind variants).
        """
        return [
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"},
            {"id": "show_status", "label": "Show Status", "style": "primary"},
            {"id": "clear_errors", "label": "Clear Error Logs", "style": "default"},
        ]

    async def execute_custom_action(
        self,
        integration: UserIntegration,
        action_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Handle a clicked button. Return ``action_result(message=...)``
        for a toast, or include ``results=[DisplayBlock, ...]`` to also
        pop the result modal.
        """
        if action_id == "reset_cursor":
            # §C — cursor write
            self.set_sync_cursor(integration, "last_timestamp", None)
            return action_result(
                message="Sync cursor reset. Next sync will pull historical data.",
                results=[
                    kv_block(
                        "Cursor",
                        {
                            "Key": "last_timestamp",
                            "New value": "None (delta-sync will start from scratch)",
                            "Instance": integration.instance_name or self.domain,
                        },
                    )
                ],
            )

        if action_id == "show_status":
            # §C — cursor read + display blocks
            cursor = self.get_sync_cursor(integration, "last_timestamp", default="<never>")
            ack_hr = self.get_sync_cursor(integration, "ack_ack_hr_at", default="<never>")
            proposals_resolved = self.get_sync_cursor(
                integration, "hitl_resolved_count", default=0
            )
            return action_result(
                message=f"Status for {integration.instance_name or self.domain}",
                results=[
                    kv_block(
                        "Sync state",
                        {
                            "Last timestamp cursor": str(cursor),
                            "Last HR acknowledgement": str(ack_hr),
                            "HITL proposals resolved": proposals_resolved,
                            "Debug enabled": bool(integration.is_debug_enabled),
                        },
                    ),
                    list_block(
                        "Active capabilities",
                        [name for name in self._enabled_capabilities(integration)],
                    ),
                ],
            )

        if action_id == "clear_errors":
            # Cosmetic only — there's no error store to clear in this
            # dummy. Real providers would advance a cursor or hit an API.
            return action_result(message="Error logs have been cleared! (Simulation)")

        raise NotImplementedError(f"Unknown action: {action_id}")

    # ==================================================================
    # §G — Notifications
    # ==================================================================

    def supports_notifications(self) -> bool:
        """Opt in. Without this override, only the platform baseline
        ('synced N records' / 'sync failed') fires.
        """
        return True

    def get_notification_types(self) -> List[NotificationTypeSpec]:
        """Statically declare the notification kinds this provider can emit.

        Surfaces them in the IntegrationDetail 'Notifications' tab and the
        central /settings/notifications rollup so users can toggle
        individual kinds without losing the rest.
        """
        return list(_NOTIFICATION_TYPES)

    async def get_notifications(
        self,
        integration: UserIntegration,
        *,
        observations: List[Any],
        context: Dict[str, Any],
    ) -> List[NotificationSpec]:
        """Inspect the just-synced observations and decide what to surface.

        The platform calls this from ``run_sync``'s finally block (after
        observations are persisted) and after webhook persistence.
        Implementations should never raise — return ``[]`` on failure.
        """
        out: List[NotificationSpec] = []

        patient_id: Optional[UUID] = integration.patient_id
        patient_url = f"/patients/{patient_id}" if patient_id else "/biomarkers"
        integration_id = str(integration.id)
        domain = self.domain

        # §E — read both quantitative (raw_value) and categorical
        # (value_string) values from the just-synced observations.
        heart_rate = self._latest_numeric(observations, "8867-4")
        bp_sys = self._latest_numeric(observations, "8480-6")
        bp_dia = self._latest_numeric(observations, "8462-4")

        # ----- (1) Threshold alert: elevated heart rate -----------------
        if heart_rate is not None and heart_rate > 100:
            spec = (
                NotificationSpec.builder(
                    title="Elevated heart rate detected",
                    body=f"Heart rate reached {int(heart_rate)} bpm (reference 60–100).",
                    category="alert",
                    severity="warning",
                )
                .type_id("elevated_heart_rate")
                .patient_id(patient_id)
                .source_ref("biomarker_code", "8867-4")
                .source_ref("reading_value", heart_rate)
                # Item 4 of integrations-sdk-improvements: collapse
                # repeated elevated-HR alerts into a single inbox entry
                # inside the TTL window (default 6h) so the user doesn't
                # get one notification per sync.
                .digest_key(
                    f"dev_dummy:elevated_heart_rate:patient/{patient_id}"
                )
                .add_link_action("View trend", patient_url)
                .add_post_action(
                    "Acknowledge",
                    endpoint=f"/integrations/{domain}/notification-action/{integration_id}/ack_hr",
                    style="ghost",
                )
                .add_post_action(
                    "Dismiss",
                    endpoint=f"/integrations/{domain}/notification-action/{integration_id}/dismiss",
                    style="default",
                )
                .display_block(
                    kv_block(
                        "Reading",
                        {
                            "Heart rate": f"{int(heart_rate)} bpm",
                            "Reference range": "60 – 100 bpm",
                            "Detected by": integration.instance_name or domain,
                        },
                    )
                )
                .build()
            )
            out.append(spec)

        # ----- (2) HITL-style prompt: elevated BP -----------------------
        if bp_sys is not None and bp_dia is not None and (bp_sys >= 130 or bp_dia >= 85):
            spec = (
                NotificationSpec.builder(
                    title="Blood pressure reading needs review",
                    body=f"BP {int(bp_sys)}/{int(bp_dia)} mmHg observed — consider confirming or re-measuring.",
                    category="hitl",
                    severity="warning",
                    type="INTEGRATION_EVENT",
                )
                .type_id("elevated_bp_review")
                .patient_id(patient_id)
                .source_ref("biomarker_codes", ["8480-6", "8462-4"])
                .add_link_action("Review reading", patient_url, style="primary")
                .add_post_action(
                    "Mark as reviewed",
                    endpoint=f"/integrations/{domain}/notification-action/{integration_id}/ack_bp",
                    style="ghost",
                )
                .display_block(
                    kv_block(
                        "Reading",
                        {
                            "Systolic": f"{int(bp_sys)} mmHg",
                            "Diastolic": f"{int(bp_dia)} mmHg",
                            "Reference": "90–120 / 60–80 mmHg",
                        },
                    )
                )
                .build()
            )
            out.append(spec)

        # ----- (3) Daily summary (table DisplayBlock, no actions) ------
        if len({heart_rate, bp_sys, bp_dia} - {None}) >= 2:
            rows: List[List[Any]] = []
            if heart_rate is not None:
                rows.append(["Heart rate", f"{int(heart_rate)} bpm", "60–100"])
            if bp_sys is not None:
                rows.append(["Systolic BP", f"{int(bp_sys)} mmHg", "90–120"])
            if bp_dia is not None:
                rows.append(["Diastolic BP", f"{int(bp_dia)} mmHg", "60–80"])
            spec = (
                NotificationSpec.builder(
                    title="Sync summary",
                    body=f"{integration.instance_name or domain}: {len(rows)} measurement(s) imported.",
                    category="system",
                    severity="info",
                )
                .type_id("daily_summary")
                .patient_id(patient_id)
                .display_block(
                    table_block("Imported readings", ["Metric", "Value", "Range"], rows)
                )
                .build()
            )
            out.append(spec)

        # ----- (4) Sensor malfunction (critical, cross-source) ---------
        # Fires when ``simulate_sensor_glitch`` produced an out-of-range
        # heart rate this cycle.
        if heart_rate is not None and heart_rate > 200:
            spec = (
                NotificationSpec.builder(
                    title="Possible sensor malfunction",
                    body=f"Heart rate reading of {int(heart_rate)} bpm is outside plausible range — worth a chat with the assistant.",
                    category="agent",
                    severity="critical",
                )
                .type_id("sensor_malfunction")
                .patient_id(patient_id)
                .add_link_action("Open chat", "/ai-assistant", style="primary")
                .build()
            )
            out.append(spec)

        return out

    async def handle_notification_action(
        self,
        integration: UserIntegration,
        action_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle clicks on POST action buttons from emitted notifications."""
        if action_id in ("ack_hr", "ack_bp"):
            # §C — cursor write. Real providers might call out to an
            # external API or write to a clinical record.
            self.set_sync_cursor(
                integration, f"ack_{action_id}_at", datetime.now(timezone.utc).isoformat()
            )
            return action_result(
                message=f"Acknowledged ({action_id}). Future occurrences will be muted for this session.",
                results=[
                    kv_block(
                        "Acknowledgement",
                        {
                            "Action": action_id,
                            "Integration": integration.instance_name or integration.provider,
                            "Acknowledged at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                ],
            )
        if action_id == "dismiss":
            return action_result(message="Dismissed. You won't see this notification again.")
        raise NotImplementedError(f"Unknown action: {action_id}")

    # ==================================================================
    # §H — Webhook ingest (push) + HMAC signature validation
    # ==================================================================

    async def handle_webhook(
        self,
        integration: UserIntegration,
        payload: Any,
        request: Any = None,
    ) -> List[ObservationCreate]:
        """Process an inbound webhook payload.

        Demonstrates:
        * HMAC-SHA256 signature validation using the Fernet-encrypted
          ``webhook_secret`` config field (§secret-lifecycle).
        * Building observations from an arbitrary posted JSON shape.
        * Logging the inbound payload via ``log_debug_payload`` (no-op
          unless debug is enabled on the instance).

        Expected payload shape (permissive — missing keys are skipped):

            {
                "metrics": [
                    {"code": "8867-4", "value": 72, "unit": "bpm", "timestamp": "2025-01-01T00:00:00Z"},
                    {"code": "94500-6", "value_string": "POSITIVE"}
                ]
            }
        """
        await self.log_debug_payload(integration, "Inbound webhook payload", payload)

        secret = self._resolve_webhook_secret(integration)

        # If a secret is configured, require a valid HMAC signature.
        if secret:
            await self._verify_signature(request, secret)

        if not isinstance(payload, dict):
            raise IntegrationDataError("Webhook payload must be a JSON object.")

        metrics = payload.get("metrics") or []
        if not isinstance(metrics, list):
            raise IntegrationDataError("`metrics` must be a list.")

        builder = self.create_observation_builder(integration)
        observations: List[ObservationCreate] = []
        now = datetime.now(timezone.utc)

        for item in metrics:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            value = item.get("value")
            value_string = item.get("value_string")
            unit = item.get("unit") or ""
            timestamp_raw = item.get("timestamp")
            effective = self._parse_dt(timestamp_raw) or now

            if not code:
                continue

            b = builder.set_biomarker(str(code), str(item.get("display") or code))
            if value is not None and value_string is None:
                # §E — quantitative
                b = b.set_value(float(value), unit)
            elif value_string is not None:
                # §E — categorical (FHIR valueString)
                b = b.set_value_string(str(value_string))
            else:
                # Skip metric without a value
                continue
            observations.append(b.set_effective_date(effective).build())

        return observations

    @staticmethod
    def _resolve_webhook_secret(integration: UserIntegration) -> Optional[str]:
        """Decrypt ``webhook_secret`` for HMAC verification.

        Demonstrates the provider-side ``decrypt_for_use`` hook on the
        config flow. The platform endpoint never sees plaintext once
        stored — only the integration itself decrypts on demand.
        """
        config = integration.user_config or {}
        wrapped = config.get("webhook_secret")
        if not wrapped or wrapped == "***":
            return None
        # ``decrypt_for_use`` is the platform-sanctioned decrypt path.
        # Importing lazily keeps the provider importable in tests that
        # don't configure ``INTEGRATION_SECRET_KEY``.
        try:
            from integrations.dev_dummy.config_flow import DevDummyConfigFlow

            decrypted = DevDummyConfigFlow().decrypt_for_use({"webhook_secret": wrapped})
            value = decrypted.get("webhook_secret")
            return str(value) if value else None
        except Exception:
            # Either the cipher isn't configured or the value is already
            # plaintext (e.g. set by a test fixture). Fall through.
            return str(wrapped) if isinstance(wrapped, str) else None

    @staticmethod
    async def _verify_signature(request: Any, secret: str) -> None:
        """Validate the ``X-DevDummy-Signature`` header.

        Format: ``HMAC-SHA256(secret, raw_body)`` hex-encoded. Raises
        :class:`IntegrationDataError` on mismatch / missing header so
        the webhook endpoint returns a 4xx.

        Delegates to :func:`integrations.sdk.webhook_security.verify_hmac_signature`
        — the SDK helper is the single canonical implementation; both
        the platform endpoint and this provider share it.
        """
        if request is None:
            raise IntegrationDataError(
                "Webhook secret is configured but no request object was "
                "supplied for signature verification."
            )
        # Provider-local header convention; providers SHOULD use the
        # SDK's ``DEFAULT_WEBHOOK_SIGNATURE_HEADERS`` when possible so
        # client libraries don't need per-provider header config.
        signature = get_signature_header(
            request.headers if hasattr(request, "headers") else {},
            names=("X-DevDummy-Signature",),
        )
        if not signature:
            raise IntegrationDataError("Missing X-DevDummy-Signature header.")

        body = await request.body() if hasattr(request, "body") else b""
        if not verify_hmac_signature(secret, body, signature):
            raise IntegrationDataError("Invalid webhook signature.")

    # ==================================================================
    # §I — Two-way API proxy (headless clients)
    # ==================================================================

    async def handle_api_request(
        self,
        integration: UserIntegration,
        path: str,
        method: str,
        request: Any,
    ) -> Dict[str, Any]:
        """Tiny REST API for headless clients (extensions, mobile apps).

        Routes implemented (purely illustrative):

        * ``GET  status``  → JSON snapshot of the instance.
        * ``GET  cursor``  → current sync cursor value.
        * ``POST reset``   → clears the cursor (no body required).
        * ``POST echo``    → echoes the posted JSON body back.

        The platform provisions a wildcard route for every enabled
        integration. UUID-in-URL is the default secret; optional HMAC
        via ``user_config['api_secret']`` is enforced by the endpoint
        itself, not the provider. Raise ``ValueError`` for user errors
        (→ 400) and ``NotImplementedError`` for unknown paths (→ 400).
        """
        if path == "status" and method == "GET":
            return {
                "domain": self.domain,
                "instance_id": str(integration.id),
                "instance_name": integration.instance_name,
                "cursor": self.get_sync_cursor(integration, "last_timestamp"),
                "capabilities": list(self._enabled_capabilities(integration)),
                "debug_enabled": bool(integration.is_debug_enabled),
            }

        if path == "cursor" and method == "GET":
            return {"cursor": self.get_sync_cursor(integration, "last_timestamp")}

        if path == "reset" and method == "POST":
            self.set_sync_cursor(integration, "last_timestamp", None)
            return {"ok": True, "cursor": None}

        if path == "echo" and method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = {}
            return {"you_sent": body}

        raise NotImplementedError(
            f"Path '{path}' with method '{method}' is not supported by {self.domain}."
        )

    # ==================================================================
    # §J — Chat tools (LangChain)
    # ==================================================================

    def supports_tools(self) -> bool:
        """Opt in to exposing tools to the chat assistant."""
        return True

    async def get_tools(self, integration: UserIntegration) -> List[Any]:
        """Return LangChain tools the chat assistant can call.

        The platform tool aggregator merges these with the built-in
        ``app/ai/tools`` before ``llm.bind_tools``. ``StructuredTool``
        is the canonical shape — async via ``coroutine=``.

        Per the contract, swallow per-instance errors and return ``[]``
        on failure so one bad instance doesn't break the whole chat
        turn.
        """
        if not (integration.user_config or {}).get("enable_tools", True):
            return []
        try:
            from langchain_core.tools import StructuredTool
            from pydantic import BaseModel, Field
        except ImportError:
            # The chat assistant stack isn't installed (e.g. running the
            # integration in a worker-only environment). Fail soft.
            return []

        instance_label = integration.instance_name or self.domain
        cursor_holder = {"integration_id": str(integration.id)}

        class LastReadingArgs(BaseModel):
            metric: str = Field(
                ...,
                description="Which metric to look up: 'heart_rate', 'systolic', 'diastolic', or 'weight'.",
            )

        async def _last_reading(metric: str) -> str:
            metric = (metric or "").lower()
            key_map = {
                "heart_rate": "8867-4",
                "systolic": "8480-6",
                "diastolic": "8462-4",
                "weight": "29463-7",
            }
            return (
                f"[dev_dummy {instance_label}] Metric '{metric}' "
                f"maps to LOINC {key_map.get(metric, 'unknown')}. "
                "This is a synthetic demo tool — no live data is returned."
            )

        class CursorArgs(BaseModel):
            pass

        async def _cursor_state() -> str:
            cursor = self.get_sync_cursor(integration, "last_timestamp", default="<never>")
            return (
                f"[dev_dummy {instance_label}] cursor={cursor!r} "
                f"(integration_id={cursor_holder['integration_id']})"
            )

        return [
            StructuredTool.from_function(
                coroutine=_last_reading,
                name=f"dev_dummy_last_reading_{self.domain}",
                description=(
                    f"Look up the last reading for a metric from the {instance_label} "
                    "dev_dummy integration (demonstration only — returns a static hint)."
                ),
                args_schema=LastReadingArgs,
            ),
            StructuredTool.from_function(
                coroutine=_cursor_state,
                name=f"dev_dummy_cursor_{self.domain}",
                description=(
                    f"Return the current sync cursor for the {instance_label} "
                    "dev_dummy integration (demonstration only)."
                ),
                args_schema=CursorArgs,
            ),
        ]

    # ==================================================================
    # §K — Clinical events (FHIR Condition / EpisodeOfCare analogue)
    # ==================================================================

    def supports_clinical_events(self) -> bool:
        return True

    async def pull_clinical_events(
        self, integration: UserIntegration
    ) -> List[ClinicalEventCreate]:
        """Emit a sample 'flu episode' clinical event.

        ``external_id`` is set so the engine dedups across syncs (the
        platform stamps ``source_integration_id`` automatically). Returns
        ``[]`` when the toggle is off or when the patient is missing.
        """
        if not (integration.user_config or {}).get("enable_clinical_events", True):
            return []
        if not integration.patient_id:
            return []

        # Make the onset date drift with the cursor so a real test against
        # the canonical ``create_event`` service looks lifelike.
        cursor_iso = self.get_sync_cursor(integration, "last_timestamp")
        onset = self._parse_dt(cursor_iso) or datetime.now(timezone.utc)
        onset = onset - timedelta(days=7)

        return [
            ClinicalEventCreate(
                patient_id=integration.patient_id,
                title="Influenza-like illness (dev_dummy demo)",
                description=(
                    "Sample clinical event synthesised by the dev_dummy integration "
                    "to exercise the supports_clinical_events / pull_clinical_events "
                    "engine path."
                ),
                onset_date=onset,
                status=ClinicalEventStatus.ACTIVE,
                coding_system=CodingSystem.SNOMED,
                code="57318006",
                event_metadata={"source": "dev_dummy", "demo": True},
                # Engine dedups on (tenant, patient, source_integration_id, external_id)
                external_id="dev_dummy_flu_episode_demo",
            )
        ]

    # ==================================================================
    # §L — Examinations (FHIR Encounter analogue)
    # ==================================================================

    def supports_examinations(self) -> bool:
        return True

    async def pull_examinations(
        self, integration: UserIntegration
    ) -> List[ExaminationCreate]:
        """Emit a sample 'annual checkup' examination.

        The category string is resolved to a concept by
        ``examination_service.create_examination`` via
        ``MedicalProcessingService.resolve_category``; set
        ``category_concept_id`` directly if you already have one.
        """
        if not (integration.user_config or {}).get("enable_examinations", True):
            return []
        if not integration.patient_id:
            return []

        cursor_iso = self.get_sync_cursor(integration, "last_timestamp")
        exam_date = (self._parse_dt(cursor_iso) or datetime.now(timezone.utc)).date()

        return [
            ExaminationCreate(
                patient_id=integration.patient_id,
                examination_date=exam_date,
                notes=(
                    "Annual checkup (dev_dummy demo). The engine writes this "
                    "through examination_service.create_examination with the "
                    "integration as the source."
                ),
                category="General Examination",
                diagnoses=["Routine check-up"],
                # Engine dedups on (tenant, patient, source_integration_id, external_id)
                external_id="dev_dummy_annual_checkup_demo",
            )
        ]

    # ==================================================================
    # §M — Catalog proposals (auto-apply)
    # ==================================================================

    def supports_catalog_proposals(self) -> bool:
        return True

    async def pull_catalog_proposals(
        self, integration: UserIntegration
    ) -> List[CatalogProposal]:
        """Auto-propose a 'Sleep Quality Score' biomarker definition.

        The engine applies this through
        ``catalog_proposal_service.apply_proposal`` — idempotent per
        ``slug`` (so re-syncing is a no-op). ``ConceptProvenance.INTEGRATION``
        is stamped where the model supports it.
        """
        if not (integration.user_config or {}).get("enable_catalog_proposals", True):
            return []

        return [
            biomarker_proposal(
                name="Dev Dummy Sleep Quality Score",
                slug="dev_dummy_sleep_quality_score",
                category="Sleep",
                coding_system="custom",
                code="DevDummySleepQuality",
                preferred_unit_symbol="score",
                reference_range_min=0.0,
                reference_range_max=100.0,
                aliases=["dd_sleep_score"],
                info=(
                    "Synthesised by the dev_dummy integration to demonstrate "
                    "auto-applied biomarker proposals (workstream F)."
                ),
                is_telemetry=True,
                confidence=0.95,
                rationale="Demo proposal — illustrates auto-apply via the SDK.",
            )
        ]

    # ==================================================================
    # §N — HITL proposals (human review)
    # ==================================================================

    def supports_hitl_proposals(self) -> bool:
        return True

    async def pull_hitl_proposals(
        self, integration: UserIntegration
    ) -> List[IntegrationProposalSpec]:
        """Queue a 'Stress Index' concept for human review.

        Unlike §M (auto-apply), the platform persists each spec as a
        PROPOSED ``IntegrationProposal`` row + fires an HITL
        notification. The user resolves via the
        ``/api/v1/integrations/instance/{id}/proposals/.../resolve``
        endpoint. Re-emitting the same spec on consecutive syncs is a
        no-op (idempotent on ``(proposal_type, proposed_payload)``).
        """
        if not (integration.user_config or {}).get("enable_hitl_proposals", True):
            return []

        # Don't re-queue once the user has resolved one — advances cursor.
        already_resolved = self.get_sync_cursor(
            integration, "hitl_stress_index_resolved", default=False
        )
        if already_resolved:
            return []

        return [
            concept_hitl_proposal(
                title="Define concept: Dev Dummy Stress Index",
                slug="dev_dummy_stress_index",
                name="Dev Dummy Stress Index",
                kind="biomarker_class",
                description=(
                    "Proposed by the dev_dummy integration to demonstrate the "
                    "HITL proposal flow (workstream G). Approve / reject from "
                    "the integration's Proposals tab."
                ),
                aliases=["dd_stress_index"],
                context={
                    "source": "dev_dummy",
                    "demo": True,
                    "integration_id": str(integration.id),
                },
            )
        ]

    async def handle_proposal_resolution(
        self,
        integration: UserIntegration,
        proposal_id: UUID,
        outcome: ProposalOutcome,
    ) -> None:
        """React to the user resolving a HITL proposal.

        Only fires on approve (reject/cancel have nothing to react to).
        We advance a cursor so we don't keep re-proposing the same thing
        on every sync.
        """
        self.set_sync_cursor(integration, "hitl_stress_index_resolved", True)
        prev = int(self.get_sync_cursor(integration, "hitl_resolved_count", default=0) or 0)
        self.set_sync_cursor(integration, "hitl_resolved_count", prev + 1)
        await self.log_debug_payload(
            integration,
            "HITL proposal resolved",
            {
                "proposal_id": str(proposal_id),
                "outcome": outcome.action,
                "applied_entity_id": str(outcome.applied_entity_id)
                if outcome.applied_entity_id
                else None,
                "error": outcome.error,
            },
        )

    # ==================================================================
    # §O — Documents
    # ==================================================================

    def supports_documents(self) -> bool:
        return True

    async def pull_documents(
        self, integration: UserIntegration
    ) -> List[DocumentPull]:
        """Synthesise a small text 'lab report' once per instance.

        Per-sync caps (20 docs / 50 MiB) protect against runaway
        providers. Idempotency is the provider's responsibility — the
        platform has no document-level dedup today, so we advance our
        own cursor and skip once we've delivered the demo doc.
        """
        if not (integration.user_config or {}).get("enable_documents", True):
            return []
        if self.get_sync_cursor(integration, "doc_demo_delivered", default=False):
            return []

        cursor_iso = self.get_sync_cursor(integration, "last_timestamp")
        report_date = (self._parse_dt(cursor_iso) or datetime.now(timezone.utc)).isoformat()
        text = (
            "DEV DUMMY DEMO LAB REPORT\n"
            f"Generated: {report_date}\n"
            "Patient: (synthetic)\n"
            "\n"
            "This is a plain-text document synthesised by the dev_dummy integration\n"
            "to demonstrate the supports_documents / pull_documents engine path.\n"
            "The platform routes it through document_service.ingest_document_bytes\n"
            "(the same write path the UI upload endpoint uses) and fires the OCR\n"
            "Celery task because include_in_extraction=True.\n"
        )
        content = text.encode("utf-8")

        # Mark delivered so we don't redeliver on every sync. Per the
        # contract, the provider owns document idempotency.
        self.set_sync_cursor(integration, "doc_demo_delivered", True)

        return [
            DocumentPull(
                filename="dev_dummy_demo_report.txt",
                content=content,
                content_type="text/plain",
                # Optional: link to the exam pulled above by its upstream id.
                examination_external_id="dev_dummy_annual_checkup_demo",
                # Item 3 of integrations-sdk-improvements: stable
                # upstream id lets the platform dedup at the DB layer
                # via ``(tenant, patient, source_integration_id,
                # external_id)``. Without this, the provider owns
                # idempotency via set_sync_cursor (which we also do,
                # belt + braces).
                external_id="dev_dummy_demo_report_v1",
                # Optional: catalog concept slug for the document category.
                # Left unset so the engine creates the doc without a category
                # (the slug may not exist in every deployment).
                category_concept_slug=None,
                include_in_extraction=True,
            )
        ]

    # ==================================================================
    # Helpers (notifications + pull_data)
    # ==================================================================

    @staticmethod
    def _enabled_capabilities(integration: UserIntegration) -> List[str]:
        """List the human-readable names of currently enabled SDK hooks.

        Used by the ``show_status`` custom action so authors can see at
        a glance which toggles are active.
        """
        cfg = integration.user_config or {}
        caps = []
        if cfg.get("generate_heart_rate", True):
            caps.append("pull_data: heart_rate")
        if cfg.get("generate_blood_pressure", True):
            caps.append("pull_data: blood_pressure")
        if cfg.get("generate_weight", False):
            caps.append("pull_data: body_weight")
        if cfg.get("generate_mood", True):
            caps.append("pull_data: mood (categorical)")
        if cfg.get("simulate_auth_error"):
            caps.append("simulate: auth_error")
        if cfg.get("simulate_rate_limit"):
            caps.append("simulate: rate_limit")
        if cfg.get("simulate_sensor_glitch"):
            caps.append("simulate: sensor_glitch")
        if cfg.get("webhook_secret"):
            caps.append("handle_webhook (HMAC-verified)")
        if cfg.get("enable_clinical_events", True):
            caps.append("pull_clinical_events")
        if cfg.get("enable_examinations", True):
            caps.append("pull_examinations")
        if cfg.get("enable_catalog_proposals", True):
            caps.append("pull_catalog_proposals (auto-apply)")
        if cfg.get("enable_hitl_proposals", True):
            caps.append("pull_hitl_proposals (human review)")
        if cfg.get("enable_documents", True):
            caps.append("pull_documents")
        if cfg.get("enable_tools", True):
            caps.append("get_tools (chat assistant)")
        # Always-on capabilities.
        caps.extend(
            [
                "supports_notifications",
                "handle_api_request",
                "push_data",
            ]
        )
        return caps

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        """Tolerant datetime parser. Accepts datetime objects, ISO strings,
        and returns None for anything else (so callers can fall back).
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def _latest_numeric(observations: List[Any], loinc: str) -> Optional[float]:
        """Return the most recent numeric ``raw_value`` for the given code.

        Fixed version of the original ``_latest_value``: read the
        Pydantic ``effective_datetime`` as a datetime (not a string) and
        ``raw_value`` directly. Categorical observations are ignored.
        """
        best: Optional[float] = None
        best_date: Optional[datetime] = None
        for obs in observations:
            code_obj = getattr(obs, "code", None) or {}
            coding = code_obj.get("coding", []) if isinstance(code_obj, dict) else []
            if not any(c.get("code") == loinc for c in coding):
                continue
            raw = getattr(obs, "raw_value", None)
            if raw is None:
                continue  # categorical (value_string) or component-only
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            eff = getattr(obs, "effective_datetime", None)
            eff_dt = DevDummyProvider._parse_dt(eff)
            if best_date is None or (eff_dt and eff_dt > best_date):
                best = value
                best_date = eff_dt
        return best

    def _simulate_api_fetch(
        self, config: Dict[str, Any], current_time: datetime
    ) -> Dict[str, Any]:
        """Build the fake 'API response' the provider pulls from."""
        mock: Dict[str, Any] = {
            "status": "success",
            "time": current_time.isoformat(),
            "metrics": [],
        }
        if config.get("generate_heart_rate", True):
            # §B — 5% chance of an out-of-range spike if the toggle is on.
            if config.get("simulate_sensor_glitch") and random.random() < 0.05:
                hr_value: float = float(random.randint(220, 260))
            else:
                hr_value = float(random.randint(60, 110))
            mock["metrics"].append(
                {"type": "heart_rate", "value": hr_value, "unit": "bpm"}
            )
        if config.get("generate_blood_pressure", True):
            mock["metrics"].extend(
                [
                    {
                        "type": "blood_pressure_systolic",
                        "value": float(random.randint(110, 140)),
                        "unit": "mmHg",
                    },
                    {
                        "type": "blood_pressure_diastolic",
                        "value": float(random.randint(70, 90)),
                        "unit": "mmHg",
                    },
                ]
            )
        if config.get("generate_weight", False):
            mock["metrics"].append(
                {
                    "type": "body_weight",
                    "value": round(random.uniform(70.0, 72.0), 1),
                    "unit": "kg",
                }
            )
        if config.get("generate_mood", True):
            # §E — categorical metric. The builder will call set_value_string.
            mock["metrics"].append(
                {
                    "type": "mood",
                    "value_string": random.choice(["good", "ok", "bad"]),
                }
            )
        return mock

    # ==================================================================
    # §A / §B / §C / §D / §E — pull_data
    # ==================================================================

    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        """Core pull hook — return ``List[ObservationCreate]``.

        Demonstrates:
        * §B — raising ``IntegrationAuthError`` / ``IntegrationRateLimitError``
          so the worker can route the sync outcome correctly.
        * §C — delta-sync cursor (5-minute increments).
        * §D — debug logging (no-op unless the toggle is on).
        * §E — quantitative + categorical observation building.
        """
        config = integration.user_config or {}
        # §B — exception mapping
        if config.get("simulate_auth_error"):
            raise IntegrationAuthError(
                "Simulated Authentication Error. Turn this off in config to resume."
            )
        if config.get("simulate_rate_limit"):
            raise IntegrationRateLimitError("Simulated Rate Limit. Try again later.")

        now = datetime.now(timezone.utc)
        # §C — cursor read
        last_sync_iso = self.get_sync_cursor(
            integration,
            "last_timestamp",
            default=(now - timedelta(hours=1)).isoformat(),
        )
        last_sync = self._parse_dt(last_sync_iso) or (now - timedelta(hours=1))
        current_time = last_sync + timedelta(minutes=5)
        if current_time > now:
            current_time = now

        raw_data = self._simulate_api_fetch(config, current_time)
        # §D — debug log (no-op unless debug enabled)
        await self.log_debug_payload(integration, "Dev Dummy API Response", raw_data)

        observations: List[ObservationCreate] = []
        builder = self.create_observation_builder(integration)
        for item in raw_data.get("metrics", []):
            metric_type = item.get("type")
            if metric_type == "heart_rate":
                observations.append(
                    builder.set_biomarker("8867-4", "Heart rate")
                    .set_value(item["value"], item["unit"], "{beats}/min")
                    .set_effective_date(current_time)
                    .set_reference_range(low=60, high=100)
                    .build()
                )
            elif metric_type == "blood_pressure_systolic":
                observations.append(
                    builder.set_biomarker("8480-6", "Systolic blood pressure")
                    .set_value(item["value"], item["unit"], "mm[Hg]")
                    .set_effective_date(current_time)
                    .set_reference_range(low=90, high=120)
                    .build()
                )
            elif metric_type == "blood_pressure_diastolic":
                observations.append(
                    builder.set_biomarker("8462-4", "Diastolic blood pressure")
                    .set_value(item["value"], item["unit"], "mm[Hg]")
                    .set_effective_date(current_time)
                    .set_reference_range(low=60, high=80)
                    .build()
                )
            elif metric_type == "body_weight":
                observations.append(
                    builder.set_biomarker("29463-7", "Body weight")
                    .set_value(item["value"], item["unit"], "kg")
                    .set_effective_date(current_time)
                    .build()
                )
            elif metric_type == "mood":
                # §E — categorical observation (FHIR valueString)
                observations.append(
                    builder.set_biomarker(
                        "dev-dummy-mood",
                        "Mood (self-reported)",
                        coding_system=CodingSystem.CUSTOM,
                    )
                    .set_value_string(item["value_string"])
                    .set_effective_date(current_time)
                    .build()
                )

        # §C — cursor write (the worker commits user_config mutations)
        self.set_sync_cursor(integration, "last_timestamp", current_time.isoformat())
        return observations

    # ==================================================================
    # §P — Lifecycle
    # ==================================================================

    async def push_data(self, integration: UserIntegration, data: Any) -> None:
        """Outbound hook. Called by the sync worker and the manual-sync
        endpoint after a successful pull. Override only when the
        integration needs to push data *outward* (e.g. mirroring back to
        a remote API). The default is a no-op.

        Here we simply log so you can see the hook fire in the worker
        logs.
        """
        await self.log_debug_payload(
            integration,
            "push_data invoked",
            {"data_summary": str(data)[:200] if data is not None else None},
        )

    async def close(self) -> None:
        """Tear down provider resources. Called on app shutdown.

        Override and call ``super().close()`` if you hold extra
        resources beyond the shared ``httpx`` client.
        """
        self.logger.info("dev_dummy provider closing (releasing shared httpx client)")
        await super().close()

    async def revoke(self, integration: UserIntegration) -> None:
        """Best-effort token revocation on disconnect.

        dev_dummy doesn't use OAuth so there's nothing to revoke —
        override is here purely to demonstrate the hook. OAuth providers
        call the RFC 7009 revocation endpoint here.
        """
        await self.log_debug_payload(
            integration,
            "revoke invoked",
            {"note": "dev_dummy has no OAuth tokens to revoke"},
        )
        return None


__all__ = ["DevDummyProvider"]
