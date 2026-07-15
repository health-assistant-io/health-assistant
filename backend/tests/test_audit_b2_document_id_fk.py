"""Regression tests for audit B2 — Observation.document_id UUID FK.

Proves:
1. document_id is a real UUID column.
2. The FK to documents.id rejects a non-existent document.
3. Deleting a document SETS NULL the linked observation's document_id (the FK
   ondelete semantics) instead of leaving a dangling string reference.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.document_model import DocumentModel
from app.models.fhir.patient import Observation
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.models.enums import Role


async def _seed_tenant_user_doc(session) -> tuple:
    tenant = TenantModel(id=uuid.uuid4(), name="B2 T", slug=f"b2-t-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    await session.flush()
    user = UserModel(
        id=uuid.uuid4(),
        email=f"b2-{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        role=Role.USER,
        tenant_id=tenant.id,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    doc = DocumentModel(
        id=uuid.uuid4(),
        filename="lab.pdf",
        file_path="/tmp/lab.pdf",
        owner_id=user.id,
        tenant_id=tenant.id,
    )
    session.add(doc)
    await session.flush()
    return tenant, user, doc


def _make_observation(tenant_id, document_id) -> Observation:
    return Observation(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        status="final",
        code={"text": "Glucose"},
        subject={"reference": "Patient/unknown"},
        document_id=document_id,
    )


@pytest.mark.asyncio
async def test_document_id_is_uuid_column():
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='fhir_observations' AND column_name='document_id'"
            )
        )
        data_type = res.scalar_one()
    assert data_type == "uuid", f"document_id should be uuid, got {data_type}"


@pytest.mark.asyncio
async def test_document_fk_rejects_nonexistent_document():
    async with AsyncSessionLocal() as session:
        tenant = TenantModel(
            id=uuid.uuid4(), name="B2 FK", slug=f"b2fk-{uuid.uuid4().hex[:8]}"
        )
        session.add(tenant)
        await session.flush()
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    _make_observation(
                        tenant_id=tenant.id, document_id=uuid.uuid4()  # no such document
                    )
                )


@pytest.mark.asyncio
async def test_delete_document_nulls_observation_document_id():
    async with AsyncSessionLocal() as session:
        tenant, user, doc = await _seed_tenant_user_doc(session)
        obs = _make_observation(tenant_id=tenant.id, document_id=doc.id)
        session.add(obs)
        await session.commit()

        # Delete the document; the FK ondelete SET NULL should null the link.
        await session.execute(
            text("DELETE FROM documents WHERE id = :did"), {"did": str(doc.id)}
        )
        await session.commit()

        # Read just the column back via a scalar select (avoids lazy-loaded
        # relationship access that would trip a greenlet on the async session).
        from sqlalchemy import select

        val = (
            await session.execute(
                select(Observation.document_id).where(Observation.id == obs.id)
            )
        ).scalar_one()
        assert val is None, (
            "Deleting a document must SET NULL the observation's document_id, "
            "not leave a dangling reference."
        )

        # Cleanup.
        await session.execute(text("DELETE FROM fhir_observations WHERE id = :oid"), {"oid": str(obs.id)})
        await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user.id)})
        await session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": str(tenant.id)})
        await session.commit()
