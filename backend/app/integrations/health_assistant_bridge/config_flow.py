from app.integrations.sdk import BaseConfigFlow

class HealthAssistantBridgeConfigFlow(BaseConfigFlow):
    domain = "health_assistant_bridge"
    
    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Health Assistant Bridge",
            "description": "Configure the bridge integration for browser extensions or mobile apps. Each instance will generate a unique secure URL bound to the selected patient.",
            "data_schema": {
                "type": "object",
                "properties": {
                    "instance_name": {
                        "type": "string", 
                        "title": "Instance Name",
                        "description": "A name for this connection (e.g., 'My Health Portal App', 'Son\\'s NHS Extension')."
                    }
                },
                "required": ["instance_name"]
            }
        }
        
    async def validate_input(self, user_input: dict) -> dict:
        if not user_input.get("instance_name"):
            raise ValueError("Instance name is required.")
        return user_input

