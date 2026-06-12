# Document model
class Document:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.filename = kwargs.get("filename")
        self.file_path = kwargs.get("file_path")
        self.owner_id = kwargs.get("owner_id")
        self.tenant_id = kwargs.get("tenant_id")
        self.status = kwargs.get("status", "uploaded")
        self.patient_id = kwargs.get("patient_id")
        self.uploaded_at = kwargs.get("uploaded_at")
        self.progress = kwargs.get("progress", 0)