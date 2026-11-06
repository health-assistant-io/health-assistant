import contextlib
import datetime
import logging
from typing import Any

from app.extensions.sdk.observation_builder import ObservationBuilder
from app.schemas.fhir.observation import ObservationCreate

from ..validation import WebhookPayloadError, validate_payload
from .base import BaseWebhookParser

logger = logging.getLogger(__name__)


class BasicParser(BaseWebhookParser):
    """Parses simple generic JSON payloads like {"type": "heart_rate", "value": 75}"""

    def parse(
        self, payload: Any, config: dict[str, Any], builder: ObservationBuilder
    ) -> list[ObservationCreate]:
        observations = []

        payload = validate_payload(payload, parser_id="basic")
        data_type = payload.get("type", "")
        value = payload.get("value")
        timestamp = payload.get("timestamp")

        if not isinstance(data_type, str):
            raise WebhookPayloadError("basic: 'type' must be a string.")
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise WebhookPayloadError("basic: 'value' must be a number.")
        if isinstance(timestamp, str) and not timestamp.isdigit():
            raise WebhookPayloadError("basic: 'timestamp' must be epoch milliseconds.")
        if data_type not in ("heart_rate", "steps", "weight"):
            raise WebhookPayloadError(
                f"basic: unknown metric type {data_type!r} (heart_rate|steps|weight)."
            )

        obs_time = datetime.datetime.now(datetime.UTC)
        if timestamp:
            # Assuming ms timestamp
            with contextlib.suppress(ValueError, TypeError):
                obs_time = datetime.datetime.fromtimestamp(int(timestamp) / 1000.0, tz=datetime.UTC)

        if data_type == "heart_rate" and config.get("track_heart_rate", True) and value is not None:
            obs = (
                builder.set_biomarker("8867-4", "Heart rate")
                .set_value(float(value), "bpm", "{beats}/min")
                .set_effective_date(obs_time)
                .build()
            )
            observations.append(obs)
        elif data_type == "steps" and config.get("track_steps", True) and value is not None:
            obs = (
                builder.set_biomarker("41950-7", "Number of steps")
                .set_value(float(value), "steps", "{steps}")
                .set_effective_date(obs_time)
                .build()
            )
            observations.append(obs)
        elif data_type == "weight" and config.get("track_weight", True) and value is not None:
            obs = (
                builder.set_biomarker("29463-7", "Body weight")
                .set_value(float(value), "kg", "kg")
                .set_effective_date(obs_time)
                .build()
            )
            observations.append(obs)

        return observations


class HomeAssistantParser(BaseWebhookParser):
    """Parses Home Assistant state-change events."""

    def parse(
        self, payload: Any, config: dict[str, Any], builder: ObservationBuilder
    ) -> list[ObservationCreate]:
        observations = []

        payload = validate_payload(payload, parser_id="home_assistant")
        entity_id = payload.get("entity_id", "")
        state = payload.get("state")
        if not isinstance(entity_id, str):
            raise WebhookPayloadError("home_assistant: 'entity_id' must be a string.")
        obs_time = datetime.datetime.now(datetime.UTC)

        if "heart_rate" in entity_id and config.get("track_heart_rate", True):
            try:
                hr_val = float(state)
                obs = (
                    builder.set_biomarker("8867-4", "Heart rate")
                    .set_value(hr_val, "bpm", "{beats}/min")
                    .set_effective_date(obs_time)
                    .build()
                )
                observations.append(obs)
            except (ValueError, TypeError):
                pass
        elif "steps" in entity_id and config.get("track_steps", True):
            try:
                steps_val = float(state)
                obs = (
                    builder.set_biomarker("41950-7", "Number of steps")
                    .set_value(steps_val, "steps", "{steps}")
                    .set_effective_date(obs_time)
                    .build()
                )
                observations.append(obs)
            except (ValueError, TypeError):
                pass

        return observations
