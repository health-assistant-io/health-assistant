import json
from pathlib import Path
from typing import Dict, Any, Optional
from app.schemas.import_data import FHIRImportConfig, ImportResult, ImportStatus


class FHIRImporter:
    """Import FHIR resources from JSON files"""
    
    def __init__(self, config: Optional[FHIRImportConfig] = None):
        self.config = config or FHIRImportConfig()
    
    async def import_from_file(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
    ) -> ImportResult:
        """Import FHIR resources from file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            return await self.import_from_dict(data, tenant_id, patient_id)
        except Exception as e:
            return ImportResult(
                job_id="",
                status=ImportStatus.FAILED,
                total_records=0,
                processed_records=0,
                failed_records=0,
                errors=[str(e)],
            )
    
    async def import_from_string(
        self,
        content: str,
        tenant_id: str,
        patient_id: Optional[str] = None,
    ) -> ImportResult:
        """Import FHIR resources from JSON string"""
        try:
            data = json.loads(content)
            return await self.import_from_dict(data, tenant_id, patient_id)
        except Exception as e:
            return ImportResult(
                job_id="",
                status=ImportStatus.FAILED,
                total_records=0,
                processed_records=0,
                failed_records=0,
                errors=[str(e)],
            )
    
    async def import_from_dict(
        self,
        data: Dict[str, Any],
        tenant_id: str,
        patient_id: Optional[str] = None,
    ) -> ImportResult:
        """Import FHIR resources from dictionary"""
        try:
            resources = []
            errors = []
            warnings = []
            created_resources = {}
            
            # Handle FHIR Bundle
            if data.get('resourceType') == 'Bundle':
                entries = data.get('entry', [])
                for entry in entries:
                    resource = entry.get('resource')
                    if resource:
                        result = self._process_resource(resource, tenant_id, patient_id)
                        if result:
                            resources.append(result)
                            resource_type = resource.get('resourceType', 'unknown')
                            created_resources[resource_type] = created_resources.get(resource_type, 0) + 1
            # Handle single resource
            elif data.get('resourceType'):
                result = self._process_resource(data, tenant_id, patient_id)
                if result:
                    resources.append(result)
                    resource_type = data.get('resourceType', 'unknown')
                    created_resources[resource_type] = 1
            
            return ImportResult(
                job_id="",
                status=ImportStatus.COMPLETED,
                total_records=len(resources),
                processed_records=len(resources),
                failed_records=0,
                errors=errors,
                warnings=warnings,
                created_resources=created_resources,
            )
        except Exception as e:
            return ImportResult(
                job_id="",
                status=ImportStatus.FAILED,
                total_records=0,
                processed_records=0,
                failed_records=0,
                errors=[str(e)],
            )
    
    def _process_resource(
        self,
        resource: Dict[str, Any],
        tenant_id: str,
        patient_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process a single FHIR resource"""
        try:
            resource_type = resource.get('resourceType')
            
            if not resource_type:
                raise ValueError("Missing resourceType")
            
            # Validate resource type if configured
            if self.config.resource_type and resource_type != self.config.resource_type:
                raise ValueError(
                    f"Expected {self.config.resource_type}, got {resource_type}"
                )
            
            # Add tenant_id to resource
            resource['tenant_id'] = tenant_id
            
            # Update patient reference if provided
            if patient_id and resource_type in ['Observation', 'DiagnosticReport', 'MedicationStatement']:
                if 'subject' not in resource:
                    resource['subject'] = {'reference': f'Patient/{patient_id}'}
                else:
                    resource['subject']['reference'] = f'Patient/{patient_id}'
            
            # Validate FHIR profile if configured
            if self.config.validate_profiles:
                self._validate_resource(resource)
            
            return resource
        except Exception as e:
            raise ValueError(f"Failed to process {resource.get('resourceType', 'unknown')}: {str(e)}")
    
    def _validate_resource(self, resource: Dict[str, Any]) -> None:
        """Validate FHIR resource structure"""
        # Basic validation - can be extended with full FHIR validation
        required_fields = {
            'Patient': ['id'],
            'Observation': ['status', 'code'],
            'DiagnosticReport': ['status', 'code'],
            'Medication': ['code'],
            'MedicationStatement': ['status', 'medication'],
        }
        
        resource_type = resource.get('resourceType')
        if resource_type in required_fields:
            for field in required_fields[resource_type]:
                if field not in resource:
                    raise ValueError(f"Missing required field: {field}")
