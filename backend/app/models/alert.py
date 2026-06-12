# Alert model
class Alert:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.type = kwargs.get("type")
        self.patient_id = kwargs.get("patient_id")
        self.threshold = kwargs.get("threshold")
        self.enabled = kwargs.get("enabled", True)
        self.last_triggered = kwargs.get("last_triggered")