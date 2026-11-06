"""Import-job lifecycle (create, progress, complete, fail, fetch)."""

from app.services.imports.types import (
    UTC,
    UUID,
    Any,
    ImportJobModel,
    JobStatus,
    RestoreResult,
    datetime,
    json,
    sa_update,
    select,
)


class JobLifecycleMixin:
    # ---------------- job lifecycle ----------------

    async def create_import_job(
        self, user_id: UUID, tenant_id: UUID, source_filename: str | None = None
    ) -> ImportJobModel:
        job = ImportJobModel(
            tenant_id=tenant_id,
            user_id=user_id,
            source_filename=source_filename,
            status=JobStatus.PENDING,
            progress=0,
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def _update_progress(
        self,
        job_id: UUID,
        progress: int,
        status: JobStatus | None = None,
        message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"progress": min(progress, 99)}
        if status:
            values["status"] = status
        await self.db.execute(
            sa_update(ImportJobModel).where(ImportJobModel.id == job_id).values(**values)
        )
        await self.db.commit()

        # Publish to Redis for live WebSocket updates (best-effort).
        try:
            from app.core.redis import publish_message

            job = await self.get_job(job_id)
            tenant_id = job.tenant_id if job else None
            if tenant_id:
                payload = {
                    "type": "import_progress",
                    "job_id": str(job_id),
                    "status": status.value if status and hasattr(status, "value") else None,
                    "progress": min(progress, 99),
                    "message": message,
                }
                await publish_message(f"tenant:{tenant_id}:tasks", json.dumps(payload))
        except Exception:
            pass

    async def _complete_job(self, job_id: UUID, result: RestoreResult) -> None:
        await self.db.execute(
            sa_update(ImportJobModel)
            .where(ImportJobModel.id == job_id)
            .values(
                status=JobStatus.COMPLETED if not result.errors else JobStatus.PARTIAL,
                progress=100,
                total_records=result.total_records,
                processed_records=result.processed_records,
                failed_records=result.failed_records,
                restore_result={
                    "created_resources": result.created_resources,
                    "updated_resources": result.updated_resources,
                    "manifest_verified": result.manifest_verified,
                    "fhir_validated": result.fhir_validated,
                },
                errors=result.errors,
                warnings=result.warnings,
                completed_at=datetime.now(UTC),
            )
        )
        await self.db.commit()

    async def _fail_job(self, job_id: UUID, error: str) -> None:
        await self.db.execute(
            sa_update(ImportJobModel)
            .where(ImportJobModel.id == job_id)
            .values(
                status=JobStatus.FAILED,
                error_message=error,
                completed_at=datetime.now(UTC),
            )
        )
        await self.db.commit()

    async def get_job(self, job_id: UUID, tenant_id: UUID | None = None) -> ImportJobModel | None:
        q = select(ImportJobModel).where(ImportJobModel.id == job_id)
        if tenant_id:
            q = q.where(ImportJobModel.tenant_id == tenant_id)
        res = await self.db.execute(q)
        return res.scalar_one_or_none()
