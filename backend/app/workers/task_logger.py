"""
Task Logging Utility - Structured logging for Celery tasks
Follows security best practices: no sensitive data, proper error handling
"""

import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID


class TaskLogger:
    """
    Structured logger for Celery tasks with security-focused design

    Security Features:
    - No API keys or sensitive data in logs
    - Structured JSON format for parsing
    - Error categorization for monitoring
    - Tenant isolation in log context
    """

    def __init__(
        self,
        task_name: str,
        task_id: str,
        tenant_id: Optional[UUID] = None,
        db=None,
    ):
        self.task_name = task_name
        self.task_id = task_id
        self.tenant_id = tenant_id
        self.db = db
        self.logger = logging.getLogger(f"celery.{task_name}")
        self.start_time = datetime.now(timezone.utc)

    async def _persist_log(
        self,
        level: str,
        message: str,
        stage: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        """Save log to database if session is available"""
        if not self.db:
            return

        if not self.tenant_id:
            self.logger.warning(
                f"Skipping DB log persistence: No tenant_id for task {self.task_id}"
            )
            return

        try:
            from app.models.task_log import TaskLog
            import uuid

            # Ensure task_id is a string for the field
            task_id_str = str(self.task_id)

            # Try to convert task_id to UUID object for resource_id if it looks like one
            res_id = None
            if isinstance(self.task_id, uuid.UUID):
                res_id = self.task_id
            elif isinstance(self.task_id, str) and len(self.task_id) == 36:
                try:
                    res_id = uuid.UUID(self.task_id)
                except ValueError:
                    pass

            log_entry = TaskLog(
                id=uuid.uuid4(),
                task_name=self.task_name,
                task_id=task_id_str,
                resource_id=res_id,
                tenant_id=self.tenant_id,
                level=level,
                stage=stage,
                message=message,
                data=data,
            )
            self.db.add(log_entry)
            await self.db.commit()
        except Exception as e:
            self.logger.error(f"Failed to persist log to DB: {e}")
            try:
                await self.db.rollback()
            except:
                pass

    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove sensitive data from log output

        Security: Never log API keys, credentials, or raw tokens
        """
        sanitized = {}
        sensitive_keys = [
            "api_key",
            "apikey",
            "token",
            "secret",
            "password",
            "credentials",
        ]

        for key, value in data.items():
            if key.lower() in sensitive_keys:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = value

        return sanitized

    def _format_log(self, level: str, message: str, **kwargs) -> Dict[str, Any]:
        """
        Create structured log entry

        Format:
        {
            "timestamp": "2026-03-17T10:30:00Z",
            "level": "INFO",
            "task_name": "ocr_document",
            "task_id": "uuid",
            "tenant_id": "uuid",
            "message": "...",
            "data": {...}
        }
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "task_name": self.task_name,
            "task_id": str(self.task_id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "message": message,
            "data": self._sanitize_data(kwargs),
            "duration_seconds": (datetime.now(timezone.utc) - self.start_time).total_seconds(),
        }

    async def log_start(self, **kwargs):
        """Log task start"""
        log_entry = self._format_log("START", "Task started", **kwargs)
        self.logger.info(json.dumps(log_entry))
        await self._persist_log("START", "Task started", data=log_entry["data"])

    async def log_progress(self, stage: str, progress: int, **kwargs):
        """Log progress update"""
        log_entry = self._format_log(
            "PROGRESS", f"Progress: {stage}", stage=stage, progress=progress, **kwargs
        )
        self.logger.info(json.dumps(log_entry))
        await self._persist_log(
            "PROGRESS", f"Progress: {stage}", stage=stage, data=log_entry["data"]
        )

    async def log_success(self, message: str = "Task completed successfully", **kwargs):
        """Log successful completion"""
        log_entry = self._format_log("SUCCESS", message, **kwargs)
        self.logger.info(json.dumps(log_entry))
        await self._persist_log("SUCCESS", message, data=log_entry["data"])

    async def log_error(self, error: Exception, stage: str = "unknown", **kwargs):
        """Log error with categorization"""
        error_type = self._categorize_error(error)
        log_entry = self._format_log(
            "ERROR",
            f"Error in {stage}",
            error_type=error_type,
            error_class=error.__class__.__name__,
            error_message=str(error),
            **kwargs,
        )
        self.logger.error(json.dumps(log_entry))
        await self._persist_log(
            "ERROR", f"Error in {stage}", stage=stage, data=log_entry["data"]
        )

    def _categorize_error(self, error: Exception) -> str:
        """
        Categorize errors for monitoring

        Categories:
        - configuration: Missing/wrong config
        - file: File not found, access issues
        - api: API errors, timeouts
        - validation: Data validation errors
        - system: System errors, timeouts
        - unknown: Uncategorized
        """
        error_map = {
            "FileNotFoundError": "file",
            "ValueError": "validation",
            "TimeoutError": "system",
            "ConnectionError": "api",
            "PermissionError": "file",
        }

        error_class = error.__class__.__name__
        return error_map.get(error_class, "unknown")


class TaskProgressTracker:
    """
    Track task progress in database for real-time monitoring

    Updates examination and document status tables
    """

    def __init__(
        self,
        db,
        document_id: Optional[UUID] = None,
        examination_id: Optional[UUID] = None,
    ):
        self.db = db
        self.document_id = document_id
        self.examination_id = examination_id

    async def update_document_status(
        self, status: str, progress: int, error_message: Optional[str] = None
    ):
        """Update document processing status"""
        from app.models.document_model import DocumentModel
        from sqlalchemy import update

        if not self.document_id:
            return

        update_data = {
            "status": status,
            "progress": progress,
            "error_message": error_message,
        }

        await self.db.execute(
            update(DocumentModel)
            .where(DocumentModel.id == self.document_id)
            .values(**update_data)
        )
        await self.db.commit()

    async def update_examination_status(
        self, status: str, progress: int, error_message: Optional[str] = None
    ):
        """Update examination extraction status"""
        from app.models.examination_model import ExaminationModel
        from sqlalchemy import update

        if not self.examination_id:
            return

        update_data = {
            "extraction_status": status,
            "extraction_progress": progress,
        }

        # Only add error_message if the column exists (we'll add it in a migration)
        # For now, we'll try to add it and catch if it fails or check if we should add it
        # Actually, let's just add it to the update_data and I will add the column to the model
        if error_message:
            update_data["error_message"] = error_message

        try:
            await self.db.execute(
                update(ExaminationModel)
                .where(ExaminationModel.id == self.examination_id)
                .values(**update_data)
            )
            await self.db.commit()
        except Exception as e:
            # Fallback if error_message column doesn't exist yet
            if "error_message" in update_data:
                del update_data["error_message"]
                await self.db.execute(
                    update(ExaminationModel)
                    .where(ExaminationModel.id == self.examination_id)
                    .values(**update_data)
                )
                await self.db.commit()

    async def mark_failed(self, error_message: str):
        """Mark task as failed with error message"""
        if self.document_id:
            await self.update_document_status("failed", 0, error_message)
        if self.examination_id:
            await self.update_examination_status("failed", 0, error_message)


class TaskTimeoutMonitor:
    """
    Monitor task execution time and detect stalls

    Security: Prevents infinite loops, resource exhaustion
    """

    def __init__(self, max_duration_seconds: int = 300):  # 5 minutes default
        self.max_duration = max_duration_seconds
        self.start_time = datetime.now(timezone.utc)

    def check_timeout(self) -> bool:
        """Check if task has exceeded max duration"""
        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return elapsed > self.max_duration

    def get_remaining_seconds(self) -> int:
        """Get remaining seconds before timeout"""
        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return max(0, int(self.max_duration - elapsed))
