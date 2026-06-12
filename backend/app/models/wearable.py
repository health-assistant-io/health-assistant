# Wearable data model
class WearableData:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.device_id = kwargs.get("device_id")
        self.timestamp = kwargs.get("timestamp")
        self.heart_rate = kwargs.get("heart_rate")
        self.steps = kwargs.get("steps")
        self.calories = kwargs.get("calories")