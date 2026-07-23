"""Config flow for the fhir_server integration (Stage 2 Pair A).

Creates a **PENDING** instance capturing the connection config (server URL,
pull bounds). OAuth tokens are NOT collected here — a separate Authorize action
runs the SMART standalone-launch round-trip and stores encrypted tokens in
``user_config["_oauth"]`` (handled by the platform OAuth routes +
:class:`~integrations.sdk.auth.OAuthTokenStore`). The instance flips to ACTIVE
on a successful callback.
"""
from typing import Any, Dict

from integrations.sdk import BaseConfigFlow


class FhirServerConfigFlow(BaseConfigFlow):
    domain = "fhir_server"

    is_oauth = True

    def get_secret_fields(self) -> list:
        return []

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Connect a FHIR Server",
            "description": (
                "Enter the FHIR base URL. For hospitals / the SMART Health IT "
                "sandbox, choose SMART authorization (you'll authorize after "
                "saving). For a local or open FHIR server (e.g. a local HAPI "
                "FHIR), choose None and skip the authorize step."
            ),
            "data_schema": {
                "type": "object",
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "default": "My Hospital",
                    },
                    "fhir_base_url": {
                        "type": "string",
                        "title": "FHIR Base URL",
                        "description": "e.g. https://r4.smarthealthit.org or http://localhost:8095/fhir",
                    },
                    "auth_mode": {
                        "type": "string",
                        "title": "Authorization",
                        "enum": ["smart", "none"],
                        "enum_descriptions": {
                            "smart": "SMART-on-FHIR (hospitals, Epic/Cerner, the SMART Health IT sandbox) — prompts you to authorize after saving",
                            "none": "None / tokenless — for local or open FHIR servers (e.g. a local HAPI FHIR); no authorize step"
                        },
                        "default": "smart",
                    },
                    "remote_patient_id": {
                        "type": "string",
                        "title": "Remote FHIR Patient ID (optional)",
                        "description": (
                            "The remote patient this integration syncs. Leave "
                            "blank to use the patient resolved by SMART "
                            "authorization, or after saving click 'Find "
                            "Patient' to search the server by name or MRN and "
                            "pick the match. Required for tokenless (None) "
                            "servers that don't resolve a patient for you."
                        ),
                    },
                    "sync_direction": {
                        "type": "string",
                        "title": "Automatic Sync Direction",
                        "description": "What the scheduled + manual 'Sync Now' should do. The Pull/Push action buttons below always run regardless of this setting. Note: switching to 'both' or 'push_only' requires re-authorizing (click Authorize again) so the SMART consent screen can request write permissions (patient/*.write).",
                        "enum": ["both", "pull_only", "push_only", "none"],
                        "enum_descriptions": {
                            "both": "Two-way — pull remote results in AND push local results out",
                            "pull_only": "Only pull from the FHIR server into Health Assistant",
                            "push_only": "Only push local observations to the FHIR server",
                            "none": "No automatic sync — trigger Pull/Push manually via the action buttons"
                        },
                        "default": "both",
                    },
                    "sync_interval": {
                        "type": "integer",
                        "title": "Sync Interval (Minutes)",
                        "default": 60,
                        "minimum": 5,
                        "maximum": 1440,
                    },
                    "time_window_months": {
                        "type": "integer",
                        "title": "Initial Pull Window (Months)",
                        "default": 12,
                        "minimum": 1,
                        "maximum": 120,
                    },
                    "categories": {
                        "type": "string",
                        "title": "Observation Categories",
                        "enum": ["both", "laboratory", "vital-signs"],
                        "enum_descriptions": {
                            "both": "Laboratory + vital signs",
                            "laboratory": "Laboratory results only",
                            "vital-signs": "Vital signs only",
                        },
                        "default": "both",
                    },
                    "pull_resources": {
                        "type": "array",
                        "title": "Record Types to Pull",
                        "description": (
                            "Which remote FHIR resources to ingest into the "
                            "patient record. 'Observation' (labs / vitals) is "
                            "always pulled and feeds the Biomarker Engine. "
                            "The rest are pulled in addition — Conditions → "
                            "Health Journeys, Encounters → Examinations, "
                            "DocumentReference → OCR, Medication → meds, "
                            "AllergyIntolerance → allergies, Immunization → "
                            "vaccines. Remove a type to opt a specific "
                            "instance out of ingesting it."
                        ),
                        "items": {
                            "type": "string",
                            "enum": [
                                "Observation",
                                "Condition",
                                "Encounter",
                                "DocumentReference",
                                "Medication",
                                "AllergyIntolerance",
                                "Immunization",
                            ],
                            "enum_descriptions": {
                                "Observation": "Labs & vitals → Biomarker Engine",
                                "Condition": "Problem list → Health Journeys",
                                "Encounter": "Visits → Examinations",
                                "DocumentReference": "Reports & PDFs → OCR extraction",
                                "Medication": "MedicationStatement + Request → Medications",
                                "AllergyIntolerance": "Allergies",
                                "Immunization": "Vaccine doses → Immunizations",
                            },
                        },
                        "uniqueItems": True,
                        "default": [
                            "Observation",
                            "Condition",
                            "Encounter",
                            "DocumentReference",
                            "Medication",
                            "AllergyIntolerance",
                            "Immunization",
                        ],
                    },
                },
                "required": ["fhir_base_url", "instance_name", "auth_mode"],
            },
        }

    async def validate_input(self, user_input: dict) -> dict:
        url = (user_input.get("fhir_base_url") or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            raise ValueError("FHIR Base URL must be a valid http(s) URL.")
        auth_mode = user_input.get("auth_mode", "smart")
        if auth_mode not in ("smart", "none"):
            raise ValueError("Authorization must be 'smart' or 'none'.")
        sync_direction = user_input.get("sync_direction", "both")
        if sync_direction not in ("both", "pull_only", "push_only", "none"):
            raise ValueError("Sync direction must be one of: both, pull_only, push_only, none.")
        window = user_input.get("time_window_months")
        if window is not None and (not isinstance(window, int) or window < 1):
            raise ValueError("Initial Pull Window must be a positive integer.")
        pull_resources = user_input.get("pull_resources")
        if pull_resources is not None:
            if not isinstance(pull_resources, list) or not pull_resources:
                raise ValueError("Record Types to Pull must be a non-empty list.")
            valid = {
                "Observation", "Condition", "Encounter", "DocumentReference",
                "Medication", "AllergyIntolerance", "Immunization",
            }
            invalid = [r for r in pull_resources if r not in valid]
            if invalid:
                raise ValueError(f"Unknown record type(s): {invalid}.")
        user_input["fhir_base_url"] = url.rstrip("/")
        user_input["auth_mode"] = auth_mode
        user_input["sync_direction"] = sync_direction
        return user_input

    def prepare_for_read(self, config: dict) -> dict:
        """Strip the OAuth token blob so tokens never reach the UI.

        The token blob is Fernet-encrypted at rest, but there's no reason to
        send it to the frontend. Connection status is reflected by the
        integration's ``status`` field (PENDING vs ACTIVE).
        """
        out = dict(config or {})
        out.pop("_oauth", None)
        return out
