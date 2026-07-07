# User model
class User:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.email = kwargs.get("email")
        self.hashed_password = kwargs.get("hashed_password")
        self.role = kwargs.get("role", "user")
        self.tenant_id = kwargs.get("tenant_id")
        self.settings = kwargs.get("settings", {})
