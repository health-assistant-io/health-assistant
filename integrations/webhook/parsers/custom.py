from typing import Any, List, Dict
import datetime
from .base import BaseWebhookParser
from app.schemas.fhir.observation import ObservationCreate
from integrations.sdk.observation_builder import ObservationBuilder
import logging
import json
from jsonpath_ng.ext import parse as parse_jsonpath

logger = logging.getLogger(__name__)

class CustomJSONPathParser(BaseWebhookParser):
    """Parses payloads using user-defined JSONPath mapping rules."""
    
    def _extract_value(self, payload: Any, path: str) -> Any:
        try:
            jsonpath_expr = parse_jsonpath(path)
            match = jsonpath_expr.find(payload)
            if match:
                return match[0].value
        except Exception as e:
            logger.warning(f"JSONPath extraction failed for '{path}': {e}")
        return None

    def _parse_time(self, time_val: Any, time_format: str) -> datetime.datetime:
        """Helper to parse time based on user configured format."""
        if not time_val:
            return datetime.datetime.now(datetime.timezone.utc)
            
        try:
            if time_format == "unix_ms":
                return datetime.datetime.fromtimestamp(float(time_val) / 1000.0, tz=datetime.timezone.utc)
            elif time_format == "unix_s":
                return datetime.datetime.fromtimestamp(float(time_val), tz=datetime.timezone.utc)
            elif time_format == "iso8601":
                time_str = str(time_val)
                if time_str.endswith('Z'):
                    time_str = time_str[:-1] + '+00:00'
                return datetime.datetime.fromisoformat(time_str)
        except Exception as e:
            logger.warning(f"Failed to parse time '{time_val}' with format '{time_format}': {e}")
            
        return datetime.datetime.now(datetime.timezone.utc)

    def parse(self, payload: Any, config: Dict[str, Any], builder: ObservationBuilder) -> List[ObservationCreate]:
        observations = []
        
        # Load the custom mapping configuration string provided by the user
        mapping_str = config.get("custom_mapping_json", "{}")
        try:
            mapping = json.loads(mapping_str)
        except json.JSONDecodeError:
            logger.error("Invalid custom_mapping_json configuration. Must be valid JSON.")
            return []

        # Example Mapping format:
        # {
        #   "heart_rate": {
        #     "value_path": "$.data.vitals.hr",
        #     "timestamp_path": "$.timestamp",
        #     "timestamp_format": "unix_ms"
        #   }
        # }
        
        if "heart_rate" in mapping and config.get("track_heart_rate", True):
            hr_map = mapping["heart_rate"]
            val = self._extract_value(payload, hr_map.get("value_path", ""))
            if val is not None:
                ts_val = self._extract_value(payload, hr_map.get("timestamp_path", ""))
                obs_time = self._parse_time(ts_val, hr_map.get("timestamp_format", "iso8601"))
                obs = builder.set_biomarker("8867-4", "Heart rate").set_value(float(val), "bpm", "{beats}/min").set_effective_date(obs_time).build()
                observations.append(obs)
                
        if "steps" in mapping and config.get("track_steps", True):
            steps_map = mapping["steps"]
            val = self._extract_value(payload, steps_map.get("value_path", ""))
            if val is not None:
                ts_val = self._extract_value(payload, steps_map.get("timestamp_path", ""))
                obs_time = self._parse_time(ts_val, steps_map.get("timestamp_format", "iso8601"))
                obs = builder.set_biomarker("41950-7", "Number of steps").set_value(float(val), "steps", "{steps}").set_effective_date(obs_time).build()
                observations.append(obs)
                
        if "weight" in mapping and config.get("track_weight", True):
            weight_map = mapping["weight"]
            val = self._extract_value(payload, weight_map.get("value_path", ""))
            if val is not None:
                ts_val = self._extract_value(payload, weight_map.get("timestamp_path", ""))
                obs_time = self._parse_time(ts_val, weight_map.get("timestamp_format", "iso8601"))
                obs = builder.set_biomarker("29463-7", "Body weight").set_value(float(val), "kg", "kg").set_effective_date(obs_time).build()
                observations.append(obs)
                
        return observations
