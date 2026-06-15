import logging
import datetime
from typing import List, Any, Dict, Optional, Literal
from app.integrations.sdk import BaseHealthProvider
from app.integrations.sdk.observation_builder import ObservationBuilder
from app.schemas.fhir.observation import ObservationCreate
from app.schemas.ai_nlp import MapResponsePayload, MetricMappingRequest
from app.models.user_integration import UserIntegration
from pydantic import BaseModel, Field
import json

logger = logging.getLogger(__name__)

# --- Payloads for Two-Way Contract ---

class ClientRecord(BaseModel):
    type: str = Field(..., description="'quantitative' or 'categorical'")
    biomarker_id: Optional[str] = Field(None, description="UUID of the mapped biomarker definition")
    code: Optional[str] = None
    coding_system: str = Field(default="custom")
    name: str
    value: Optional[float] = None
    value_string: Optional[str] = None
    unit: Optional[str] = None
    timestamp: Optional[str] = None
    reference_range: Optional[Dict[str, float]] = None
    interpretation: Optional[str] = None
    performer: Optional[str] = None

class SyncPayload(BaseModel):
    client_version: str
    source_system: str
    cursor: Optional[str] = None
    records: List[ClientRecord]

class MapRequestPayload(BaseModel):
    unmapped_metrics: List[MetricMappingRequest]


class HealthAssistantBridgeProvider(BaseHealthProvider):
    domain = "health_assistant_bridge"
    
    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        # This is a bridge integration driven by the client. It does not actively pull data on a schedule.
        return []

    async def handle_api_request(self, integration: UserIntegration, path: str, method: str, request: Any) -> Dict[str, Any]:
        """Handle two-way API requests from headless clients."""
        config = integration.user_config or {}
        
        # Log the request details for debugging
        await self.log_debug_payload(
            integration, 
            f"API Request: {method} /{path}", 
            {"path": path, "method": method}
        )

        if path == "status" and method == "GET":
            return {
                "status": "active",
                "integration_id": str(integration.id),
                "last_synced_at": integration.last_synced_at.isoformat() if integration.last_synced_at else None,
                "cursor": self.get_sync_cursor(integration, "last_timestamp")
            }
            
        elif path == "map" and method == "POST":
            # The client asks the backend to map raw names to existing catalog entries via LLM
            try:
                payload_data = await request.json()
                map_request = MapRequestPayload(**payload_data)
            except Exception as e:
                raise ValueError(f"Invalid payload format: {e}")
                
            return await self._handle_map_request(integration, map_request)

        elif path == "sync" and method == "POST":
            # The client pushes data here
            try:
                payload_data = await request.json()
                sync_payload = SyncPayload(**payload_data)
            except Exception as e:
                raise ValueError(f"Invalid Sync payload format: {e}")
                
            await self.log_debug_payload(integration, f"Sync Payload ({sync_payload.source_system})", payload_data)
            
            # Use universal parsing logic
            builder = self.create_observation_builder(integration)
            observations = self._parse_records(sync_payload.records, builder, str(integration.id), integration.instance_name)
            
            try:
                inserted_count = await self._process_and_save_observations(integration, observations)
                
                # Update the cursor if provided by the client
                if sync_payload.cursor:
                    self.set_sync_cursor(integration, "last_timestamp", sync_payload.cursor)
                
                return {
                    "success": True, 
                    "metrics_synced": inserted_count,
                    "message": "Data synchronized successfully"
                }
            except Exception as e:
                logger.error(f"[{self.domain}] Sync failed: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        
        else:
            raise NotImplementedError(f"Path '{path}' with method '{method}' is not supported by the bridge API.")

    def _parse_records(self, records: List[ClientRecord], builder: ObservationBuilder, integration_id: str, instance_name: str) -> List[ObservationCreate]:
        observations = []
        for record in records:
            dt = datetime.datetime.now(datetime.timezone.utc)
            if record.timestamp:
                try:
                    dt = datetime.datetime.fromisoformat(record.timestamp.replace('Z', '+00:00'))
                except ValueError:
                    pass
            
            from app.models.enums import CodingSystem
            
            system_map = {
                "loinc": CodingSystem.LOINC,
                "snomed": CodingSystem.SNOMED,
                "custom": CodingSystem.CUSTOM
            }
            coding_system = system_map.get(record.coding_system.lower(), CodingSystem.CUSTOM)
            
            # Extract biomarker ID directly if provided
            biomarker_id = None
            if hasattr(record, "biomarker_id") and record.biomarker_id:
                try:
                    from uuid import UUID
                    biomarker_id = UUID(record.biomarker_id)
                except ValueError:
                    pass
            
            code_str = record.code or "unknown"
            
            obs_builder = builder.set_biomarker(
                code_str, 
                record.name, 
                coding_system=coding_system,
                biomarker_id=biomarker_id
            ).set_effective_date(dt)
            
            if record.type == "quantitative" and record.value is not None:
                obs_builder.set_value(record.value, record.unit or "", record.unit or "")
            elif record.type == "categorical" and record.value_string:
                # The ObservationBuilder doesn't have a direct categorical method exposed simply, 
                # but we can set it in the underlying data dictionary directly or just use raw_value
                obs_builder._data["value_string"] = record.value_string
                
            if record.reference_range:
                obs_builder.set_reference_range(
                    low=record.reference_range.get("low"),
                    high=record.reference_range.get("high")
                )
                
            if record.interpretation:
                obs_builder.set_interpretation(record.interpretation)
                
            obs = obs_builder.build()
            
            # Ensure the performer explicitly links to this integration instance so it appears in the UI
            obs.performer = [{
                "type": "Integration", 
                "display": record.performer or instance_name or "Health Assistant Bridge",
                "reference": f"Integration/{integration_id}"
            }]
                
            observations.append(obs)
            
        return observations

    async def _handle_map_request(self, integration: UserIntegration, map_request: MapRequestPayload) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import select
        from app.models.biomarker_model import BiomarkerDefinition
        from app.services.ai_provider_service import AIProviderService

        async with AsyncSessionLocal() as db:
            # 1. Fetch existing biomarkers
            bio_defs = await db.execute(select(BiomarkerDefinition).where(BiomarkerDefinition.tenant_id == integration.tenant_id))
            existing_bios = bio_defs.scalars().all()
            
            catalog_str = "\n".join([f"ID: {b.id} | Name: {b.name} | Code: {b.code} | Aliases: {', '.join(b.aliases or [])}" for b in existing_bios])
            
            # 2. Setup LLM Orchestrator
            ai_service = AIProviderService(db)
            try:
                nlp_extractor = await ai_service.get_nlp_extractor(tenant_id=integration.tenant_id, user_id=integration.user_id)
            except Exception as e:
                logger.error(f"Failed to get NLP extractor for mapping: {e}")
                raise ValueError("AI mapping service is currently unavailable.")
                
            # 3. Delegate to central NLP component
            try:
                result = await nlp_extractor.map_external_metrics(
                    raw_metrics=map_request.unmapped_metrics,
                    existing_catalog_str=catalog_str
                )
                return result.model_dump()
            except NotImplementedError as e:
                # Re-raise NotImplementedError to be caught by the router and returned as 400
                raise e
            except Exception as e:
                logger.error(f"LLM Mapping failed: {e}")
                raise ValueError(f"Failed to perform AI mapping: {str(e)}")

    async def _process_and_save_observations(self, integration: UserIntegration, observations_data: List[ObservationCreate]) -> int:
        """Helper to process and save observations to DB."""
        if not observations_data:
            return 0
            
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import select
        from app.models.fhir import Observation
        from app.models.biomarker_model import BiomarkerDefinition
        from app.models.telemetry_model import TelemetryDataModel
        from app.models.user_integration import IntegrationSyncLog
        from app.services.fhir_service import map_observations_to_biomarkers

        count = 0
        start_time = datetime.datetime.now(datetime.timezone.utc)
        
        async with AsyncSessionLocal() as db:
            try:
                observations = []
                for obs_data in observations_data:
                    obs_dict = obs_data.model_dump(exclude_unset=True) if hasattr(obs_data, "model_dump") else obs_data.dict(exclude_unset=True) if hasattr(obs_data, "dict") else obs_data
                    obs = Observation(**obs_dict)
                    observations.append(obs)
                    
                await map_observations_to_biomarkers(db, observations)
                
                # Fetch all definitions used
                b_ids = list(set([obs.biomarker_id for obs in observations if obs.biomarker_id]))
                b_defs_map = {}
                if b_ids:
                    stmt = select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids))
                    res = await db.execute(stmt)
                    for b in res.scalars().all():
                        b_defs_map[b.id] = b

                telemetry_records = []
                fhir_records = []

                for obs in observations:
                    is_telemetry = False
                    if obs.biomarker_id and obs.biomarker_id in b_defs_map:
                        is_telemetry = b_defs_map[obs.biomarker_id].is_telemetry
                    
                    if is_telemetry:
                        slug = b_defs_map[obs.biomarker_id].slug.lower() if b_defs_map[obs.biomarker_id].slug else ""
                        val = getattr(obs, "normalized_value", None) or getattr(obs, "raw_value", None) or (obs.value_quantity.get("value") if obs.value_quantity else None)
                        
                        hr = val if slug == "8867-4" or "heart-rate" in slug else None
                        steps = val if slug == "41950-7" or "steps" in slug else None
                        cal = val if "calories" in slug else None
                        
                        data_payload = {}
                        if not hr and not steps and not cal:
                            data_payload[slug] = val
                            data_payload[f"{slug}_unit"] = obs.value_quantity.get("unit", "") if obs.value_quantity else ""

                        telemetry_records.append(TelemetryDataModel(
                            tenant_id=integration.tenant_id,
                            device_id=integration.instance_name or integration.provider,
                            timestamp=obs.effective_datetime,
                            heart_rate=hr,
                            steps=steps,
                            calories=cal,
                            data=data_payload if data_payload else None
                        ))
                    else:
                        fhir_records.append(obs)
                
                if telemetry_records:
                    db.add_all(telemetry_records)
                if fhir_records:
                    db.add_all(fhir_records)
                count += len(telemetry_records) + len(fhir_records)
                
                # We do NOT db.add(integration) here because it is already attached 
                # to the outer session provided by the FastAPI Dependency `Depends(get_db)`.
                # If we add it to the inner `AsyncSessionLocal()`, SQLAlchemy throws an error.
                integration.last_synced_at = datetime.datetime.now(datetime.timezone.utc)

                sync_log = IntegrationSyncLog(
                    integration_id=integration.id,
                    tenant_id=integration.tenant_id,
                    status="success",
                    records_synced=count,
                    started_at=start_time,
                    completed_at=integration.last_synced_at
                )
                db.add(sync_log)
                
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"Error saving observations from bridge: {e}")
                
                sync_log = IntegrationSyncLog(
                    integration_id=integration.id,
                    tenant_id=integration.tenant_id,
                    status="failed",
                    records_synced=0,
                    started_at=start_time,
                    completed_at=datetime.datetime.now(datetime.timezone.utc),
                    error_message=str(e)
                )
                db.add(sync_log)
                await db.commit()
                raise e

        return count

    def get_custom_actions(self) -> List[Dict[str, str]]:
        return [
            {"id": "get_api_details", "label": "Connection Details", "style": "primary"},
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"}
        ]
        
    async def execute_custom_action(self, integration: UserIntegration, action_id: str, **kwargs) -> Dict[str, Any]:
        if action_id == "get_api_details":
             api_url = f"/api/v1/integrations/{self.domain}/api/{integration.id}/status"
             return {"message": f"Bridge API is ready. Connect your client to: {api_url}"}
             
        if action_id == "reset_cursor":
            self.set_sync_cursor(integration, "last_timestamp", None)
            return {"message": "Sync cursor has been reset. The client will pull all historical data on the next sync."}

        raise NotImplementedError(f"Action '{action_id}' is not supported.")
