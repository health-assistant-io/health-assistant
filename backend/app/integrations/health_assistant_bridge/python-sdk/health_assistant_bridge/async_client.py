import httpx
import warnings
from typing import List

from . import __version__
from .models import (
    BridgeStatus,
    MetricMappingRequest,
    MapRequestPayload,
    MapResponsePayload,
    SyncPayload,
    SyncResponse
)

class AsyncHealthAssistantBridgeClient:
    """Asynchronous client for the Health Assistant Universal Bridge integration."""
    
    def __init__(self, base_url: str, integration_id: str):
        self.base_url = base_url.rstrip("/")
        self.integration_id = integration_id
        
    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v1/integrations/health_assistant_bridge/api/{self.integration_id}"
        
    async def get_status(self) -> BridgeStatus:
        """Check the connection status and retrieve the current sync cursor."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.api_url}/status")
            response.raise_for_status()
            data = response.json()
            status_obj = BridgeStatus(**data)
            
            if status_obj.latest_sdks and status_obj.latest_sdks.get("python"):
                latest = status_obj.latest_sdks["python"]
                if latest != __version__:
                    warnings.warn(f"You are using SDK version {__version__}, but the latest available is {latest}. Please consider updating.")
                    
            return status_obj
        
    async def request_mapping(self, metrics: List[MetricMappingRequest]) -> MapResponsePayload:
        """Ask the Health Assistant AI to map unrecognized metrics."""
        payload = MapRequestPayload(unmapped_metrics=metrics)
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.api_url}/map", json=payload.model_dump(exclude_unset=True))
            response.raise_for_status()
            return MapResponsePayload(**response.json())
        
    async def sync_data(self, payload: SyncPayload) -> SyncResponse:
        """Push data into the Health Assistant platform."""
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.api_url}/sync", json=payload.model_dump(exclude_unset=True))
            response.raise_for_status()
            return SyncResponse(**response.json())
