# Tenant model
class Tenant:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.name = kwargs.get("name")
        self.settings = kwargs.get("settings", {})
        self.created_at = kwargs.get("created_at")