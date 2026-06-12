DOCUMENT_CATEGORIES = [
    {"id": "laboratory-tests", "name": "Laboratory Tests"},
    {"id": "imaging-radiology", "name": "Imaging & Radiology"},
    {"id": "vital-signs", "name": "Vital Signs"},
    {"id": "blood-laboratory", "name": "Blood Laboratory"},
    {"id": "urine-laboratory", "name": "Urine Laboratory"},
    {"id": "cardiology", "name": "Cardiology"},
    {"id": "neurology", "name": "Neurology"},
    {"id": "ophthalmology", "name": "Ophthalmology"},
    {"id": "gastroenterology", "name": "Gastroenterology"},
    {"id": "pulmonology", "name": "Pulmonology"},
    {"id": "dentistry", "name": "Dentistry"},
    {"id": "pathology", "name": "Pathology"},
    {"id": "audiology", "name": "Audiology"},
    {"id": "auto-generated", "name": "Unmapped Results"},
    {"id": "other", "name": "Other"},
]

CATEGORY_MAPPING = {cat["id"]: cat["name"] for cat in DOCUMENT_CATEGORIES}
CATEGORY_NAMES = [cat["name"] for cat in DOCUMENT_CATEGORIES]
