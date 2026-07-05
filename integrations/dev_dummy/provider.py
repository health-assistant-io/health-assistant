"""Dev Dummy provider — a feature-rich reference for SDK capabilities.

This provider demonstrates EVERY notification pattern the platform supports:

1. **Threshold alert** (alert / warning): heart rate > 100 → critical badge,
   action buttons (View trend link + Acknowledge POST + Dismiss POST).
2. **HITL-style prompt** (hitl / warning): high BP → "Review reading" with
   link to the biomarker and a dismiss action.
3. **Daily summary** (system / info): table DisplayBlock of all readings,
   no actions — just informational.
4. **Anomaly / agent-style** (agent / info): outliers flagged as "needs
   attention" with an "Open chat" link (demonstrates cross-source).
5. **Custom user prompt** (integration / info): echoes the integration
   instance_name to demonstrate the integration_id source_ref routing.

It also implements ``handle_notification_action`` so the Acknowledge /
Dismiss buttons actually do something (cursor reset + ack flag).
"""
import random
from datetime import datetime, timezone, timedelta
from typing import List, Any, Dict, Optional
from uuid import UUID

from integrations.sdk import (
    BaseHealthProvider,
    NotificationSpec,
    NotificationTypeSpec,
)
from integrations.sdk.display import kv_block, table_block, action_result
from integrations.sdk.exceptions import IntegrationAuthError, IntegrationRateLimitError
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


class DevDummyProvider(BaseHealthProvider):
    domain = "dev_dummy"

    # ------------------------------------------------------------------
    # Custom actions (existing — config-flow buttons on the integration
    # detail page). Unchanged.
    # ------------------------------------------------------------------

    def get_custom_actions(self) -> List[Dict[str, str]]:
        return [
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"},
            {"id": "clear_errors", "label": "Clear Error Logs", "style": "default"},
        ]

    async def execute_custom_action(self, integration: UserIntegration, action_id: str, **kwargs) -> Dict[str, Any]:
        if action_id == "reset_cursor":
            self.set_sync_cursor(integration, "last_timestamp", None)
            return {"message": "Sync cursor reset successfully. Next sync will pull historical data."}
        if action_id == "clear_errors":
            return {"message": "Error logs have been cleared! (Simulation)"}
        raise NotImplementedError()

    # ------------------------------------------------------------------
    # Notifications — NEW
    # ------------------------------------------------------------------

    def supports_notifications(self) -> bool:
        # Opt in. Without this override, only the platform baseline
        # ("synced N records" / "sync failed") fires.
        return True

    def get_notification_types(self) -> List[NotificationTypeSpec]:
        """Statically declare the notification kinds this provider can emit.
        Surfaces them in the IntegrationDetail 'Notifications' tab and the
        central /settings/notifications rollup so users can toggle individual
        kinds without losing the rest.
        """
        return list(_NOTIFICATION_TYPES)

    async def get_notifications(
        self,
        integration: UserIntegration,
        *,
        observations: List[Any],
        context: Dict[str, Any],
    ) -> List[NotificationSpec]:
        """Inspect the just-synced observations and decide what to surface."""
        # When testing thresholds, only consider heart_rate + blood_pressure.
        # The observations list is List[ObservationCreate] — duck-typed access
        # to .raw_value / .code keeps us decoupled from the Pydantic shape.
        out: List[NotificationSpec] = []

        # Resolve deep-link helpers. Note: the biomarker-detail route is
        # `/biomarkers/details/<UUID>` (definition id, NOT LOINC code, and
        # NOT patient-scoped). The provider doesn't know the definition UUID
        # at notification-emit time (only the LOINC), so we deep-link to the
        # patient dashboard — the user can find the biomarker there in context.
        patient_id: Optional[UUID] = integration.patient_id
        patient_url = f"/patients/{patient_id}" if patient_id else "/biomarkers"
        integration_id = str(integration.id)
        domain = self.domain

        # Gather the readings we care about
        heart_rate = self._latest_value(observations, "8867-4")
        bp_sys = self._latest_value(observations, "8480-6")
        bp_dia = self._latest_value(observations, "8462-4")

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
                .add_link_action(
                    "View trend",
                    patient_url,
                )
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
                .add_link_action(
                    "Review reading",
                    patient_url,
                    style="primary",
                )
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
        # Always emit when we have ≥2 distinct metrics — purely informational.
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
                .display_block(table_block("Imported readings", ["Metric", "Value", "Range"], rows))
                .build()
            )
            out.append(spec)

        # ----- (4) Agent-style outlier flag (cross-source demo) --------
        # If any reading is more than 2× the upper reference, surface as an
        # agent-style prompt with an "Open chat" deep link. Demonstrates that
        # integration-driven notifications can use any category.
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
            # Record an ack flag in the sync state — real providers might
            # call out to an external API or write to a clinical record.
            self.set_sync_cursor(integration, f"ack_{action_id}_at", datetime.now(timezone.utc).isoformat())
            return action_result(
                message=f"Acknowledged ({action_id}). Future occurrences within this session will be muted.",
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _latest_value(observations: List[Any], loinc: str) -> Optional[float]:
        """Return the most recent numeric value for the given LOINC code."""
        best: Optional[float] = None
        best_date: Optional[datetime] = None
        for obs in observations:
            code_obj = getattr(obs, "code", None) or {}
            coding = code_obj.get("coding", []) if isinstance(code_obj, dict) else []
            for c in coding:
                if c.get("code") == loinc:
                    # effective_datetime may be on the Pydantic model directly
                    eff_raw = getattr(obs, "effective_datetime", None)
                    try:
                        eff = datetime.fromisoformat(eff_raw.replace("Z", "+00:00")) if eff_raw else None
                    except Exception:
                        eff = None
                    if best_date is None or (eff and eff > best_date):
                        vq = getattr(obs, "value_quantity", None) or {}
                        val = vq.get("value") if isinstance(vq, dict) else None
                        if val is not None:
                            best = float(val)
                            best_date = eff
        return best

    # ------------------------------------------------------------------
    # pull_data (existing synthetic data — unchanged)
    # ------------------------------------------------------------------

    def _simulate_api_fetch(self, config: Dict[str, Any], current_time: datetime) -> Dict[str, Any]:
        mock = {"status": "success", "time": current_time.isoformat(), "metrics": []}
        if config.get("generate_heart_rate", True):
            mock["metrics"].append({"type": "heart_rate", "value": float(random.randint(60, 110)), "unit": "bpm"})
        if config.get("generate_blood_pressure", True):
            mock["metrics"].extend([
                {"type": "blood_pressure_systolic", "value": float(random.randint(110, 140)), "unit": "mmHg"},
                {"type": "blood_pressure_diastolic", "value": float(random.randint(70, 90)), "unit": "mmHg"},
            ])
        if config.get("generate_weight", False):
            mock["metrics"].append({"type": "body_weight", "value": round(random.uniform(70.0, 72.0), 1), "unit": "kg"})
        return mock

    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        config = integration.user_config or {}
        if config.get("simulate_auth_error"):
            raise IntegrationAuthError("Simulated Authentication Error. Turn this off in config to resume.")
        if config.get("simulate_rate_limit"):
            raise IntegrationRateLimitError("Simulated Rate Limit. Try again later.")

        now = datetime.now(timezone.utc)
        last_sync_iso = self.get_sync_cursor(integration, "last_timestamp", default=(now - timedelta(hours=1)).isoformat())
        last_sync = datetime.fromisoformat(last_sync_iso)
        current_time = last_sync + timedelta(minutes=5)
        if current_time > now:
            current_time = now

        raw_data = self._simulate_api_fetch(config, current_time)
        await self.log_debug_payload(integration, "Dev Dummy API Response", raw_data)

        observations: List[ObservationCreate] = []
        builder = self.create_observation_builder(integration)
        for item in raw_data.get("metrics", []):
            if item["type"] == "heart_rate":
                observations.append(
                    builder.set_biomarker("8867-4", "Heart rate")
                    .set_value(item["value"], item["unit"], "{beats}/min")
                    .set_effective_date(current_time)
                    .set_reference_range(low=60, high=100)
                    .build()
                )
            elif item["type"] == "blood_pressure_systolic":
                observations.append(
                    builder.set_biomarker("8480-6", "Systolic blood pressure")
                    .set_value(item["value"], item["unit"], "mm[Hg]")
                    .set_effective_date(current_time)
                    .set_reference_range(low=90, high=120)
                    .build()
                )
            elif item["type"] == "blood_pressure_diastolic":
                observations.append(
                    builder.set_biomarker("8462-4", "Diastolic blood pressure")
                    .set_value(item["value"], item["unit"], "mm[Hg]")
                    .set_effective_date(current_time)
                    .set_reference_range(low=60, high=80)
                    .build()
                )
            elif item["type"] == "body_weight":
                observations.append(
                    builder.set_biomarker("29463-7", "Body weight")
                    .set_value(item["value"], item["unit"], "kg")
                    .set_effective_date(current_time)
                    .build()
                )

        self.set_sync_cursor(integration, "last_timestamp", current_time.isoformat())
        return observations

    async def push_data(self, integration: UserIntegration, data: Any):
        print(f"[DevDummy Push] user={integration.user_id}: {data}")
