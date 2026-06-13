from typing import Any, List, Dict
import datetime
from .base import BaseWebhookParser
from app.schemas.fhir.observation import ObservationCreate
from app.integrations.sdk.observation_builder import ObservationBuilder
import logging

logger = logging.getLogger(__name__)

class HcWebhookParser(BaseWebhookParser):
    """Parses payloads from the HC Webhook App."""
    
    def _parse_time(self, time_str: str) -> datetime.datetime:
        """Helper to parse ISO8601 strings to datetime."""
        try:
            # Handle standard Z format
            if time_str.endswith('Z'):
                time_str = time_str[:-1] + '+00:00'
            return datetime.datetime.fromisoformat(time_str)
        except Exception:
            return datetime.datetime.now(datetime.timezone.utc)
            
    def parse(self, payload: Any, config: Dict[str, Any], builder: ObservationBuilder) -> List[ObservationCreate]:
        observations = []
        
        if not isinstance(payload, dict):
            return []
            
        # The app might send "source": "health_connect", but the test payload might omit it.
        # We'll loosen the restriction to process if any expected keys exist or if it's explicitly "test": true
        if payload.get("source") != "health_connect" and not payload.get("test") and "steps" not in payload and "heart_rate" not in payload:
            return []
            
        # Parse Steps
        if config.get("track_steps", True):
            for step_record in payload.get("steps", []):
                count = step_record.get("count")
                if count is not None:
                    start_time = self._parse_time(step_record.get("start_time", ""))
                    end_time = self._parse_time(step_record.get("end_time", ""))
                    obs = builder.set_biomarker("41950-7", "Number of steps") \
                        .set_value(float(count), "steps", "{steps}") \
                        .set_effective_date(start_time) \
                        .build()
                    # Add period information if needed, though effective_date usually marks start or middle
                    observations.append(obs)
                    
        # Parse Heart Rate
        if config.get("track_heart_rate", True):
            for hr_record in payload.get("heart_rate", []):
                bpm = hr_record.get("bpm")
                if bpm is not None:
                    time_val = self._parse_time(hr_record.get("time", ""))
                    obs = builder.set_biomarker("8867-4", "Heart rate") \
                        .set_value(float(bpm), "bpm", "{beats}/min") \
                        .set_effective_date(time_val) \
                        .build()
                    observations.append(obs)
                    
            for rhr_record in payload.get("resting_heart_rate", []):
                bpm = rhr_record.get("bpm")
                if bpm is not None:
                    time_val = self._parse_time(rhr_record.get("time", ""))
                    obs = builder.set_biomarker("40443-4", "Heart rate resting") \
                        .set_value(float(bpm), "bpm", "{beats}/min") \
                        .set_effective_date(time_val) \
                        .build()
                    observations.append(obs)
                    
        # Parse Weight
        if config.get("track_weight", True):
            for weight_record in payload.get("weight", []):
                kg = weight_record.get("kilograms")
                if kg is not None:
                    time_val = self._parse_time(weight_record.get("time", ""))
                    obs = builder.set_biomarker("29463-7", "Body weight") \
                        .set_value(float(kg), "kg", "kg") \
                        .set_effective_date(time_val) \
                        .build()
                    observations.append(obs)
                    
        # Parse Sleep (Extracting total duration and stages)
        if config.get("track_sleep", True):
            for sleep_record in payload.get("sleep", []):
                duration = sleep_record.get("duration_seconds")
                if duration is not None:
                    end_time = self._parse_time(sleep_record.get("session_end_time", ""))
                    # Convert seconds to hours
                    hours = duration / 3600.0
                    obs = builder.set_biomarker("93832-4", "Sleep duration") \
                        .set_value(hours, "h", "h") \
                        .set_effective_date(end_time) \
                        .build()
                    observations.append(obs)
                    
                # Parse sleep stages
                for stage_record in sleep_record.get("stages", []):
                    stage_duration = stage_record.get("duration_seconds")
                    stage_name = stage_record.get("stage")
                    if stage_duration is not None and stage_name:
                        stage_end = self._parse_time(stage_record.get("end_time", ""))
                        stage_hours = stage_duration / 3600.0
                        obs = builder.set_biomarker(f"sleep-stage-{stage_name.lower()}", f"Sleep duration - {stage_name.capitalize()}") \
                            .set_value(stage_hours, "h", "h") \
                            .set_effective_date(stage_end) \
                            .build()
                        observations.append(obs)

        # Parse Oxygen Saturation
        if config.get("track_oxygen", True):
            for spo2_record in payload.get("oxygen_saturation", []):
                percentage = spo2_record.get("percentage")
                if percentage is not None:
                    time_val = self._parse_time(spo2_record.get("time", ""))
                    obs = builder.set_biomarker("59408-5", "Oxygen saturation") \
                        .set_value(float(percentage), "%", "%") \
                        .set_effective_date(time_val) \
                        .build()
                    observations.append(obs)
                    
        # Parse Heart Rate Variability
        if config.get("track_hrv", True):
            for hrv_record in payload.get("heart_rate_variability", []):
                rmssd = hrv_record.get("rmssd_millis")
                if rmssd is not None:
                    time_val = self._parse_time(hrv_record.get("time", ""))
                    obs = builder.set_biomarker("80404-7", "Heart rate variability (RMSSD)") \
                        .set_value(float(rmssd), "ms", "ms") \
                        .set_effective_date(time_val) \
                        .build()
                    observations.append(obs)
                    
        return observations
