from typing import Dict, Any
from datetime import datetime
from app.models.fhir.observation import Observation


class FHIRMapper:
    """Maps extracted entities to FHIR resources"""

    @staticmethod
    def map_text_to_diagnostic_report(
        text: str, patient_id: str, tenant_id: str, document_id: str
    ) -> Dict[str, Any]:
        """Convert extracted text to DiagnosticReport"""

        return {
            "resourceType": "DiagnosticReport",
            "tenant_id": tenant_id,
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "34108-1",
                            "display": "Outpatient Note",
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "34108-1",
                        "display": "Outpatient Note",
                    }
                ]
            },
            "subject": {"reference": f"Patient/{patient_id}"},
            "effective_datetime": datetime.now().isoformat(),
            "issued": datetime.now().isoformat(),
            "presented_form": [
                {
                    "url": f"/api/v1/documents/{document_id}/download",
                    "contentType": "text/plain",
                }
            ],
            "conclusion": text[:1000] if text else "No summary available",
        }
