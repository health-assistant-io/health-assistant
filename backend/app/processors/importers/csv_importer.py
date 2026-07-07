import csv
import io
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from app.schemas.import_data import CSVImportConfig, ImportResult, ImportStatus


class CSVImporter:
    """Import data from CSV files"""

    def __init__(self, config: Optional[CSVImportConfig] = None):
        self.config = config or CSVImportConfig()

    async def import_from_file(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
    ) -> ImportResult:
        """Import data from CSV file"""
        try:
            with open(file_path, "r", encoding=self.config.encoding) as f:
                content = f.read()

            return await self.import_from_string(content, tenant_id, patient_id)
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
        """Import data from CSV string content"""
        try:
            reader = csv.DictReader(
                io.StringIO(content), delimiter=self.config.delimiter
            )

            records = []
            errors = []
            warnings = []

            for row_num, row in enumerate(reader, start=2):
                try:
                    record = self._process_row(row, row_num)
                    if record:
                        records.append(record)
                except Exception as e:
                    errors.append(f"Row {row_num}: {str(e)}")

            return ImportResult(
                job_id="",
                status=ImportStatus.COMPLETED if not errors else ImportStatus.PARTIAL,
                total_records=len(records) + len(errors),
                processed_records=len(records),
                failed_records=len(errors),
                errors=errors,
                warnings=warnings,
                created_resources={"observations": len(records)},
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

    def _process_row(
        self,
        row: Dict[str, str],
        row_num: int,
    ) -> Optional[Dict[str, Any]]:
        """Process a single CSV row"""
        try:
            # Map columns based on configuration
            mapped = {}
            for target_field, source_column in self.config.column_mappings.items():
                if source_column in row:
                    value = row[source_column]
                    mapped[target_field] = self._convert_value(value, target_field)

            # Auto-detect common fields if no mappings provided
            if not self.config.column_mappings:
                mapped = self._auto_map_row(row)

            return mapped
        except Exception as e:
            raise ValueError(f"Failed to process row {row_num}: {str(e)}")

    def _auto_map_row(self, row: Dict[str, str]) -> Dict[str, Any]:
        """Auto-map common CSV column names to FHIR fields"""
        mapped = {}

        # Common column name mappings
        field_mappings = {
            "date": "effective_datetime",
            "datetime": "effective_datetime",
            "timestamp": "effective_datetime",
            "value": "value",
            "result": "value",
            "unit": "unit",
            "units": "unit",
            "biomarker": "biomarker",
            "test": "biomarker",
            "parameter": "biomarker",
            "patient": "patient_id",
            "patient_id": "patient_id",
            "mrn": "patient_id",
        }

        for source, target in field_mappings.items():
            for key in row.keys():
                if key.lower() == source:
                    mapped[target] = self._convert_value(row[key], target)
                    break

        return mapped

    def _convert_value(self, value: str, field: str) -> Any:
        """Convert string value to appropriate type"""
        if not value or value.strip() == "":
            return None

        # Date/datetime fields
        if "date" in field.lower() or "time" in field.lower():
            return self._parse_datetime(value)

        # Numeric fields
        if field in ["value", "value_quantity"]:
            return self._parse_numeric(value)

        # Default to string
        return value.strip()

    def _parse_datetime(self, value: str) -> Optional[datetime]:
        """Parse datetime string"""
        formats = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%d/%m/%Y",
            "%m/%d/%Y",
        ]

        if self.config.date_format:
            formats.insert(0, self.config.date_format)

        for fmt in formats:
            try:
                from datetime import timezone

                dt = datetime.strptime(value.strip(), fmt)
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
            except ValueError:
                continue

        # If all formats fail, return original string
        return value.strip()

    def _parse_numeric(self, value: str) -> Any:
        """Parse numeric value"""
        try:
            # Remove common non-numeric characters
            cleaned = value.strip().replace(",", "")

            # Try integer first
            if "." not in cleaned:
                return int(cleaned)

            # Try float
            return float(cleaned)
        except ValueError:
            return value.strip()
