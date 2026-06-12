# Dashboard data model
class DashboardData:
    def __init__(self, **kwargs):
        self.recent_documents = kwargs.get("recent_documents", [])
        self.upcoming_appointments = kwargs.get("upcoming_appointments", [])
        self.alerts = kwargs.get("alerts", [])
        self.summary = kwargs.get("summary", {})