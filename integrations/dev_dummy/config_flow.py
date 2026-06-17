from typing import Dict, Any
from integrations.sdk import BaseConfigFlow

class DevDummyConfigFlow(BaseConfigFlow):
    domain = "dev_dummy"
    
    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Dev Dummy",
            "description": "Configure metrics, sync interval, and error simulation for testing the SDK.",
            "data_schema": {
                "type": "object",
                "properties": {
                    "sync_interval": {
                        "type": "integer",
                        "title": "Sync Interval (Minutes)",
                        "default": 15,
                        "minimum": 1,
                        "maximum": 1440
                    },
                    "generate_heart_rate": {
                        "type": "boolean",
                        "title": "Generate Heart Rate (bpm)",
                        "default": True
                    },
                    "generate_blood_pressure": {
                        "type": "boolean",
                        "title": "Generate Blood Pressure (mmHg)",
                        "default": True
                    },
                    "generate_weight": {
                        "type": "boolean",
                        "title": "Generate Body Weight (kg)",
                        "default": False
                    },
                    "simulate_auth_error": {
                        "type": "boolean",
                        "title": "Simulate Auth Error (Test SDK Exceptions)",
                        "default": False
                    },
                    "simulate_rate_limit": {
                        "type": "boolean",
                        "title": "Simulate Rate Limit (Test Background Retries)",
                        "default": False
                    },
                    "debug_mode": {
                        "type": "boolean",
                        "title": "Enable Debug Mode (Log payload to console)",
                        "default": True
                    }
                },
                "required": ["sync_interval"]
            }
        }
        
    async def validate_input(self, user_input: dict) -> dict:
        if not isinstance(user_input.get("sync_interval"), int) or user_input["sync_interval"] <= 0:
            raise ValueError("Sync interval must be a positive integer.")
        return user_input
