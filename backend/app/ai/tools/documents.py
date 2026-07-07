"""Document tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3).
"""

import json
from typing import Any, List
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import and_, desc, select

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.document_model import DocumentModel


@register_chat_tool("documents")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_document_content(document_id: str) -> str:
        """Fetch the full extracted text content of a specific document (e.g., a lab report or clinical note).
        Use this to read detailed findings that aren't available in the structured summary."""
        try:
            doc_uuid = UUID(document_id)
        except ValueError:
            return "Invalid document ID format."

        result = await ctx.db.execute(
            select(DocumentModel).where(
                and_(
                    DocumentModel.id == doc_uuid,
                    DocumentModel.tenant_id == ctx.tenant_id,
                    DocumentModel.patient_id == ctx.patient_id,
                )
            )
        )
        doc = result.scalars().first()
        if not doc:
            return "Document not found or access denied."

        if not doc.extracted_text:
            return f"Document '{doc.filename}' has no extracted text content (Status: {doc.status})."

        return json.dumps(
            {
                "id": str(doc.id),
                "filename": doc.filename,
                "content": doc.extracted_text,
            }
        )

    @tool
    async def get_patient_documents(limit: int = 10) -> str:
        """Fetch a list of documents (PDFs, images) uploaded for the patient.
        Returns filenames, upload dates, and status."""
        result = await ctx.db.execute(
            select(DocumentModel)
            .where(
                and_(
                    DocumentModel.patient_id == ctx.patient_id,
                    DocumentModel.tenant_id == ctx.tenant_id,
                )
            )
            .order_by(desc(DocumentModel.created_at))
            .limit(limit)
        )
        docs = result.scalars().all()
        summary = []
        for d in docs:
            summary.append(
                {
                    "id": str(d.id),
                    "filename": d.filename,
                    "status": d.status,
                    "date": d.created_at.isoformat() if d.created_at else None,
                }
            )
        return json.dumps(summary)

    return [get_document_content, get_patient_documents]
