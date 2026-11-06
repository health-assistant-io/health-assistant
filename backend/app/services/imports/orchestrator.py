"""Import orchestration: run_import, zip and bare-JSON entrypoints."""

import logging

from app.services.imports.types import (
    UUID,
    FHIRImportConfig,
    JobStatus,
    RestoreResult,
    json,
    validate_bundle,
    zipfile,
)

logger = logging.getLogger(__name__)


class OrchestratorMixin:
    # ---------------- orchestrator ----------------

    async def run_import(
        self,
        job_id: UUID,
        archive_path: str,
        owner_id: UUID,
        config: FHIRImportConfig | None = None,
    ) -> RestoreResult:
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Import job {job_id} not found")
        tenant_id = job.tenant_id
        if not tenant_id:
            raise ValueError("Import job has no tenant_id")

        result = RestoreResult(job_id=str(job_id), status=JobStatus.PROCESSING)
        self._job_id = job_id
        try:
            await self._update_progress(job_id, 5, JobStatus.PROCESSING)

            if zipfile.is_zipfile(archive_path):
                (
                    created,
                    updated,
                    errors,
                    warnings,
                    manifest_verified,
                    fhir_validated,
                ) = await self._restore_from_zip(archive_path, tenant_id, owner_id, job_id, config)
            else:
                (
                    created,
                    updated,
                    errors,
                    warnings,
                    manifest_verified,
                    fhir_validated,
                ) = await self._restore_from_bare_json(archive_path, tenant_id, job_id, config)

            result.created_resources = created
            result.updated_resources = updated
            result.errors = errors
            result.warnings = warnings
            result.manifest_verified = manifest_verified
            result.fhir_validated = fhir_validated
            result.processed_records = sum(created.values()) + sum(updated.values())
            result.failed_records = len(errors)
            result.total_records = result.processed_records + result.failed_records
            result.status = JobStatus.COMPLETED if not errors else JobStatus.PARTIAL

            await self._complete_job(job_id, result)
            return result
        except Exception as e:
            logger.exception(f"Import job {job_id} failed")
            await self._fail_job(job_id, str(e))
            result.status = JobStatus.FAILED
            result.errors = [str(e)]
            raise

    async def _restore_from_zip(
        self,
        archive_path: str,
        tenant_id: UUID,
        owner_id: UUID,
        job_id: UUID,
        config: FHIRImportConfig | None = None,
    ) -> tuple[dict[str, int], dict[str, int], list[str], list[str], bool, bool]:
        created: dict[str, int] = {}
        updated: dict[str, int] = {}
        errors: list[str] = []
        warnings: list[str] = []
        manifest_verified = False
        fhir_validated = False

        with zipfile.ZipFile(archive_path, "r") as zf:
            ok, _manifest, merrors = self.verify_manifest_from_zip(zf)
            manifest_verified = ok
            if not ok:
                errors.extend(merrors)
                warnings.append(
                    "Manifest verification failed; continuing with best-effort restore."
                )

            await self._update_progress(job_id, 20)

            try:
                bundle_bytes = zf.read("fhir/bundle.json")
                bundle = json.loads(bundle_bytes)
            except KeyError:
                bundle = None
                warnings.append("No fhir/bundle.json in archive (catalog/sidecar-only backup).")

            id_remap: dict[str, str] = {}
            if bundle:
                ok, verrors = validate_bundle(bundle)
                fhir_validated = ok
                if not ok:
                    errors.extend(verrors)
                _brr = await self.restore_fhir_bundle(
                    bundle,
                    tenant_id,
                    validate=False,
                    config=config,
                    actor_user_id=owner_id,
                    source_job_id=job_id,
                )
                created.update(_brr.created)
                updated.update(_brr.updated)
                errors.extend(_brr.errors)
                warnings.extend(_brr.warnings)
                id_remap = _brr.id_remap
                await self._update_progress(job_id, 50)

            # Order matters: concepts + anatomy are FK targets for biomarker
            # classes, examination categories, and edge endpoints, so they must
            # materialize first. Edges are polymorphic and reference
            # concepts/anatomy/biomarkers/examinations, so they go last.
            for name in [
                "concepts.json",
                "anatomy.json",
                "biomarker_definitions.json",
                "clinical_event_types.json",
                "examinations.json",
                "clinical_events.json",
                "notification_triggers.json",
                "telemetry.json",
                "integrations.json",
                "ai_config.json",
                "concept_edges.json",
            ]:
                try:
                    payload = json.loads(zf.read(f"nonfhir/{name}"))
                except KeyError:
                    continue
                c, e, w = await self.restore_sidecar(name, payload, tenant_id, id_remap)
                created.update(c)
                errors.extend(e)
                warnings.extend(w)
                await self._update_progress(job_id, 70)

            try:
                documents_meta = json.loads(zf.read("nonfhir/documents.json"))
                n = await self.restore_documents(documents_meta, zf, tenant_id, id_remap, owner_id)
                created["documents"] = n
            except KeyError:
                pass

        await self.db.commit()
        return created, updated, errors, warnings, manifest_verified, fhir_validated

    async def _restore_from_bare_json(
        self,
        path: str,
        tenant_id: UUID,
        job_id: UUID,
        config: FHIRImportConfig | None = None,
    ) -> tuple[dict[str, int], dict[str, int], list[str], list[str], bool, bool]:
        created: dict[str, int] = {}
        updated: dict[str, int] = {}
        errors: list[str] = []
        warnings: list[str] = []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if data.get("resourceType") == "Bundle":
            ok, verrors = validate_bundle(data)
            if not ok:
                errors.extend(verrors)
            _brr = await self.restore_fhir_bundle(
                data,
                tenant_id,
                validate=False,
                config=config,
                source_job_id=job_id,
            )
            created.update(_brr.created)
            updated.update(_brr.updated)
            errors.extend(_brr.errors)
            warnings.extend(_brr.warnings)
            await self.db.commit()
            return created, updated, errors, warnings, False, ok

        if "biomarkers" in data or "units" in data:
            n = await self._restore_biomarker_catalog(data, tenant_id)
            created["biomarker_definitions"] = n
            await self.db.commit()
            return created, updated, errors, warnings, False, False

        errors.append("Unrecognized JSON: not a FHIR Bundle and not a catalog payload.")
        return created, updated, errors, warnings, False, False
