from app.integrations.sdk import BaseConfigFlow
import os

class WebhookConfigFlow(BaseConfigFlow):
    domain = "webhook"
    
    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Webhook",
            "description": "Create a new webhook instance. After saving, view details to get your unique URL.",
            "data_schema": {
                "type": "object",
                "required": ["instance_name", "parser_type"],
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "description": "Give this webhook connection a friendly name (e.g., 'My Scale', 'Phone Tracker')",
                        "default": "Universal Webhook"
                    },
                    "parser_type": {
                        "type": "string",
                        "title": "Payload Format Parser",
                        "description": "Select how the incoming JSON data should be parsed.",
                        "enum": ["life_dashboard", "basic", "home_assistant", "custom"],
                        "enum_descriptions": {
                            "life_dashboard": "Extracts Steps, Heart Rate (Active/Resting), Body Weight, and Sleep Duration from the open-source Life Dashboard Android app.",
                            "basic": "Expects a simple flat JSON payload. Valid 'type' values are 'heart_rate', 'steps', and 'weight'.",
                            "home_assistant": "Parses Home Assistant state-change events. Matches 'entity_id' for 'heart_rate' and 'steps'.",
                            "custom": "Provides a text area below to map custom JSONPath expressions for incoming data."
                        },
                        "default": "life_dashboard"
                    },
                    "custom_mapping_json": {
                        "type": "string",
                        "format": "json",
                        "title": "Custom JSONPath Mapping",
                        "description": "Provide a JSON configuration to map incoming fields to Health Assistant metrics.",
                        "default": '{\n  "heart_rate": {\n    "value_path": "$.vitals.hr",\n    "timestamp_path": "$.timestamp",\n    "timestamp_format": "unix_ms"\n  }\n}'
                    },
                    "track_heart_rate": {
                        "type": "boolean",
                        "title": "Track Heart Rate",
                        "default": True
                    },
                    "track_steps": {
                        "type": "boolean",
                        "title": "Track Steps",
                        "default": True
                    },
                    "track_sleep": {
                        "type": "boolean",
                        "title": "Track Sleep",
                        "default": True
                    },
                    "track_weight": {
                        "type": "boolean",
                        "title": "Track Weight",
                        "default": True
                    }
                }
            }
        }
        
    async def validate_input(self, user_input: dict) -> dict:
        return user_input
