from typing import Any, List, Dict
import datetime
from .base import BaseWebhookParser
from app.schemas.fhir.observation import ObservationCreate
from integrations.sdk.observation_builder import ObservationBuilder
import logging

logger = logging.getLogger(__name__)

class BasicParser(BaseWebhookParser):
    """Parses simple generic JSON payloads like {"type": "heart_rate", "value": 75}"""
    
    def parse(self, payload: Any, config: Dict[str, Any], builder: ObservationBuilder) -> List[ObservationCreate]:
        observations = []
        
        if not isinstance(payload, dict):
            logger.warning(f"BasicParser received non-dict payload: {payload}")
            return []
            
        data_type = payload.get("type", "")
        value = payload.get("value")
        timestamp = payload.get("timestamp")
        
        obs_time = datetime.datetime.now(datetime.timezone.utc)
        if timestamp:
            try:
                # Assuming ms timestamp
                obs_time = datetime.datetime.fromtimestamp(int(timestamp) / 1000.0, tz=datetime.timezone.utc)
            except (ValueError, TypeError):
                pass
                
        if data_type == "heart_rate" and config.get("track_heart_rate", True) and value is not None:
            obs = builder.set_biomarker("8867-4", "Heart rate").set_value(float(value), "bpm", "{beats}/min").set_effective_date(obs_time).build()
            observations.append(obs)
        elif data_type == "steps" and config.get("track_steps", True) and value is not None:
            obs = builder.set_biomarker("41950-7", "Number of steps").set_value(float(value), "steps", "{steps}").set_effective_date(obs_time).build()
            observations.append(obs)
        elif data_type == "weight" and config.get("track_weight", True) and value is not None:
            obs = builder.set_biomarker("29463-7", "Body weight").set_value(float(value), "kg", "kg").set_effective_date(obs_time).build()
            observations.append(obs)
            
        return observations

class HomeAssistantParser(BaseWebhookParser):
    """Parses Home Assistant state-change events."""
    
    def parse(self, payload: Any, config: Dict[str, Any], builder: ObservationBuilder) -> List[ObservationCreate]:
        observations = []
        
        if not isinstance(payload, dict):
            return []
            
        entity_id = payload.get("entity_id", "")
        state = payload.get("state")
        obs_time = datetime.datetime.now(datetime.timezone.utc)
        
        if "heart_rate" in entity_id and config.get("track_heart_rate", True):
            try:
                hr_val = float(state)
                obs = builder.set_biomarker("8867-4", "Heart rate").set_value(hr_val, "bpm", "{beats}/min").set_effective_date(obs_time).build()
                observations.append(obs)
            except (ValueError, TypeError):
                pass
        elif "steps" in entity_id and config.get("track_steps", True):
            try:
                steps_val = float(state)
                obs = builder.set_biomarker("41950-7", "Number of steps").set_value(steps_val, "steps", "{steps}").set_effective_date(obs_time).build()
                observations.append(obs)
            except (ValueError, TypeError):
                pass
                
        return observations
