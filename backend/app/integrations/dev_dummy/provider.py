import random
from datetime import datetime, timezone, timedelta
from typing import List, Any, Dict
from app.integrations.sdk import BaseHealthProvider
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration
from app.integrations.sdk.exceptions import IntegrationAuthError, IntegrationRateLimitError

class DevDummyProvider(BaseHealthProvider):
    domain = "dev_dummy"
    
    def get_custom_actions(self) -> List[Dict[str, str]]:
        return [
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"},
            {"id": "clear_errors", "label": "Clear Error Logs", "style": "default"}
        ]
        
    async def execute_custom_action(self, integration: UserIntegration, action_id: str, **kwargs) -> Dict[str, Any]:
        if action_id == "reset_cursor":
            self.set_sync_cursor(integration, "last_timestamp", None)
            return {"message": "Sync cursor reset successfully. Next sync will pull historical data."}
            
        if action_id == "clear_errors":
            # Just an example of returning a UI message
            return {"message": "Error logs have been cleared! (Simulation)"}
            
        raise NotImplementedError()

    def _simulate_api_fetch(self, config: Dict[str, Any], current_time: datetime) -> Dict[str, Any]:
        """Simulate fetching a raw JSON payload from a third-party API."""
        mock_api_response = {
            "status": "success", 
            "time": current_time.isoformat(),
            "metrics": []
        }
        
        if config.get("generate_heart_rate", True):
            mock_api_response["metrics"].append({
                "type": "heart_rate", 
                "value": float(random.randint(60, 100)), 
                "unit": "bpm"
            })
            
        if config.get("generate_blood_pressure", True):
            mock_api_response["metrics"].extend([
                {"type": "blood_pressure_systolic", "value": float(random.randint(110, 130)), "unit": "mmHg"},
                {"type": "blood_pressure_diastolic", "value": float(random.randint(70, 85)), "unit": "mmHg"}
            ])
            
        if config.get("generate_weight", False):
            mock_api_response["metrics"].append({
                "type": "body_weight", 
                "value": round(random.uniform(70.0, 72.0), 1), 
                "unit": "kg"
            })
            
        return mock_api_response

    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        config = integration.user_config or {}
        
        # 1. Error Simulation Testing
        if config.get("simulate_auth_error"):
            raise IntegrationAuthError("Simulated Authentication Error. Please turn this off in config to resume syncing.")
        if config.get("simulate_rate_limit"):
            raise IntegrationRateLimitError("Simulated Rate Limit. Try again later.")
            
        # 2. State Management (Cursor for Delta Syncs)
        now = datetime.now(timezone.utc)
        last_sync_iso = self.get_sync_cursor(integration, "last_timestamp", default=(now - timedelta(hours=1)).isoformat())
        last_sync = datetime.fromisoformat(last_sync_iso)
        
        current_time = last_sync + timedelta(minutes=5)
        if current_time > now:
            current_time = now
            
        # 3. FETCH RAW DATA
        raw_data = self._simulate_api_fetch(config, current_time)
        
        # 4. DEBUG LOGGING
        await self.log_debug_payload(integration, "Dev Dummy API Response", raw_data)
        
        # 5. MAP TO FHIR
        observations = []
        builder = self.create_observation_builder(integration)
        
        for item in raw_data.get("metrics", []):
            if item["type"] == "heart_rate":
                observations.append(
                    builder
                    .set_biomarker("8867-4", "Heart rate")
                    .set_value(item["value"], item["unit"], "{beats}/min")
                    .set_effective_date(current_time)
                    .set_reference_range(low=60, high=100)
                    .build()
                )
            elif item["type"] == "blood_pressure_systolic":
                observations.append(
                    builder
                    .set_biomarker("8480-6", "Systolic blood pressure")
                    .set_value(item["value"], item["unit"], "mm[Hg]")
                    .set_effective_date(current_time)
                    .set_reference_range(low=90, high=120)
                    .build()
                )
            elif item["type"] == "blood_pressure_diastolic":
                observations.append(
                    builder
                    .set_biomarker("8462-4", "Diastolic blood pressure")
                    .set_value(item["value"], item["unit"], "mm[Hg]")
                    .set_effective_date(current_time)
                    .set_reference_range(low=60, high=80)
                    .build()
                )
            elif item["type"] == "body_weight":
                observations.append(
                    builder
                    .set_biomarker("29463-7", "Body weight")
                    .set_value(item["value"], item["unit"], "kg")
                    .set_effective_date(current_time)
                    .build()
                )
                
        # 6. UPDATE CURSOR
        self.set_sync_cursor(integration, "last_timestamp", current_time.isoformat())
            
        return observations
        
    async def push_data(self, integration: UserIntegration, data: Any):
        # Simply print to console for dev dummy
        print(f"[DevDummy Push] Sending data to dev sink for user {integration.user_id}: {data}")
