"""CSV/OCR/FHIR-string import paths kept for endpoint compatibility."""

from app.services.imports.types import (
    UUID,
    CSVImportConfig,
    FHIRImportConfig,
    ImportResult,
    ImportStatus,
    Path,
    json,
)


class CsvOcrImportMixin:
    # ---------------- CSV/OCR import (kept for endpoint compat) ----------------

    async def import_from_csv(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: str | None = None,
        config: CSVImportConfig | None = None,
    ) -> ImportResult:
        from app.processors.importers.csv_importer import CSVImporter

        importer = CSVImporter(config)
        result = await importer.import_from_file(file_path, tenant_id, patient_id)
        return result

    async def import_from_ocr(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: str | None = None,
        user_id: str | None = None,
    ) -> ImportResult:
        """OCR-import one file through the scoped AI gateway.

        The OCR processor (and with it the provider, base URL, model and BYOK
        key) resolves from the ``ocr`` task assignment via
        :meth:`AIProviderService.get_ocr_processor` — the endpoint never feeds
        model credentials of its own, and the model factory stays the only
        place a chat model is constructed (ADR-0008).
        """
        try:
            from app.ai.providers.service import AIProviderService

            ocr_processor = await AIProviderService(self.db).get_ocr_processor(
                UUID(tenant_id),
                UUID(user_id) if user_id else None,
            )
            _text = await ocr_processor.extract_text(file_path)
            return ImportResult(
                job_id="",
                status=ImportStatus.COMPLETED,
                total_records=1,
                processed_records=1,
                failed_records=0,
                created_resources={"documents": 1},
                summary=f"Extracted text from {file_path.name}",
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

    async def import_from_fhir(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: str | None = None,
        config: FHIRImportConfig | None = None,
    ) -> ImportResult:
        from uuid import UUID as _UUID

        try:
            tid = _UUID(tenant_id)
        except (ValueError, TypeError):
            return ImportResult(
                job_id="",
                status=ImportStatus.FAILED,
                total_records=0,
                processed_records=0,
                failed_records=0,
                errors=["Invalid tenant_id"],
            )
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("resourceType") != "Bundle":
            data = {
                "resourceType": "Bundle",
                "type": "collection",
                "entry": [{"resource": data}] if data.get("resourceType") else [],
            }
        if patient_id:
            for entry in data.get("entry", []):
                r = entry.get("resource") or {}
                if r.get("resourceType") in (
                    "Observation",
                    "DiagnosticReport",
                    "MedicationStatement",
                ):
                    r.setdefault("subject", {})["reference"] = f"Patient/{patient_id}"
        _brr = await self.restore_fhir_bundle(data, tid, validate=True, config=config)
        await self.db.commit()
        c, u, e, w = _brr.created, _brr.updated, _brr.errors, _brr.warnings
        total = sum(c.values()) + sum(u.values())
        return ImportResult(
            job_id="",
            status=ImportStatus.COMPLETED if not e else ImportStatus.PARTIAL,
            total_records=total,
            processed_records=total,
            failed_records=len(e),
            created_resources=c,
            updated_resources=u,
            errors=e,
            warnings=w,
        )
