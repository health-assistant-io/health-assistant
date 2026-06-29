import pytest
import uuid
import datetime
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient
from app.main import app
from app.core.security import get_current_user
from app.core.database import get_db
from app.models.examination_model import ExaminationModel
from app.models.document_model import DocumentModel
from app.models.fhir.patient import Observation
from app.models.fhir.medication import Medication
from app.schemas.user import TokenData


def override_get_current_user():
    uid = uuid.uuid4()
    tid = uuid.uuid4()
    return TokenData(user_id=uid, sub=str(uid), tenant_id=tid, role="USER")


@pytest.mark.asyncio
async def test_examination_thorough_deletion(async_client: AsyncClient):
    """Verify that deleting an examination also triggers deletion of associated documents and clinical data"""
    from app.models.fhir.patient import Patient as FHIRPatient

    uid = uuid.uuid4()
    tid = uuid.uuid4()
    current_user_obj = TokenData(user_id=uid, sub=str(uid), tenant_id=tid, role="USER")

    def local_override():
        return current_user_obj

    app.dependency_overrides[get_current_user] = local_override

    exam_id = uuid.uuid4()
    patient_id = uuid.uuid4()

    # Mock examination
    mock_exam = ExaminationModel(
        id=exam_id, tenant_id=tid, patient_id=patient_id, examination_date=datetime.date.today()
    )

    db_mock = AsyncMock()
    db_mock.add = MagicMock()

    # 1. Mock the first select for the examination
    res_exam = MagicMock()
    res_exam.scalar_one_or_none.return_value = mock_exam

    # 2. Mock patient select with matching user_id so check_patient_access passes
    mock_patient = FHIRPatient(
        id=patient_id,
        tenant_id=tid,
        user_id=uid,
        name={"text": "Test Patient"},
        gender="other",
    )
    res_patient = MagicMock()
    res_patient.scalar_one_or_none.return_value = mock_patient

    # 3. Mock the documents select
    mock_doc = DocumentModel(id=uuid.uuid4(), file_path="/tmp/test.pdf")
    res_docs = MagicMock()
    res_docs.scalars.return_value.all.return_value = [mock_doc]

    # Create dynamic side effect function to be resilient to query counts & order
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        if "from examinations" in q_str:
            return res_exam
        if "from fhir_patients" in q_str:
            return res_patient
        if "from documents" in q_str:
            return res_docs
        return MagicMock()

    db_mock.execute.side_effect = mock_execute

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    # We need to mock delete_document service call because it touches the filesystem
    with patch(
        "app.services.document_service_db.delete_document", new_callable=AsyncMock
    ) as mock_delete_doc:
        response = await async_client.delete(f"/api/v1/examinations/{exam_id}")

        assert response.status_code == 200
        assert "deleted successfully" in response.json()["message"]

        # Verify document deletion was called with trigger_cumulative=False
        mock_delete_doc.assert_called_once_with(
            str(mock_doc.id), db_mock, trigger_cumulative=False
        )

        # Verify exam itself was deleted
        db_mock.delete.assert_called_with(mock_exam)
        db_mock.commit.assert_called()

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_cumulative_extraction_error_logging():
    """Verify that cumulative extraction task logs errors to the database"""
    from app.workers.ai_tasks import cumulative_extraction
    async def _cumulative_extraction_async(examination_id: str):
        return await cumulative_extraction.__wrapped__.__wrapped__(None, examination_id)

    exam_id = str(uuid.uuid4())

    # Mock database setup
    db_mock = AsyncMock()
    db_mock.add = MagicMock()

    exam_id_uuid = UUID(exam_id) if isinstance(exam_id, str) else exam_id
    tenant_id_uuid = uuid.uuid4()

    # Use a real model instance instead of MagicMock to avoid attribute issues
    mock_exam = ExaminationModel(
        id=exam_id_uuid,
        tenant_id=tenant_id_uuid,
        diagnoses=[],
        impressions="",
        examination_date=datetime.date.today(),
    )

    res_exam = MagicMock()
    res_exam.scalar_one_or_none.return_value = mock_exam
    db_mock.execute.return_value = res_exam

    # Also need to mock subsequent database calls within the task
    # Stage 1: Aggregate Text select
    res_docs = MagicMock()
    res_docs.scalars.return_value.all.return_value = [
        MagicMock(extracted_text="Test", status="completed")
    ]

    # Stage 3: Catalogs select
    res_cats = MagicMock()
    res_cats.scalars.return_value.all.return_value = []

    # Generic result for updates
    res_update = MagicMock()

    # Create a dynamic, resilient mock_exec to handle different query sequences
    async def mock_exec(query, *args, **kwargs):
        q_str = str(query).lower()
        if "from examinations" in q_str:
            return res_exam
        if "from documents" in q_str:
            return res_docs
        if "from fhir_patients" in q_str or "from fhir_observations" in q_str or "from fhir_medications" in q_str or "from biomarker_definitions" in q_str or "from units" in q_str:
            return res_cats
        return res_update

    db_mock.execute.side_effect = mock_exec

    # Mock get_async_session to return our mock session and engine
    with patch("app.workers.ai_tasks.get_async_session", return_value=(db_mock, AsyncMock())):
        # Mock NLP extractor to raise an error
        with patch(
            "app.ai.providers.service.AIProviderService.get_nlp_extractor",
            new_callable=AsyncMock,
        ) as mock_nlp:
            mock_nlp.side_effect = Exception("AI Provider Timeout")

            # We expect the task to re-raise the exception but also update the DB
            with pytest.raises(Exception) as excinfo:
                await _cumulative_extraction_async(exam_id)

            assert "AI Provider Timeout" in str(excinfo.value)

            # Verify the progress tracker was called to mark failed with error message
            # The update() call should have been executed
            found_update = False
            for call in db_mock.execute.call_args_list:
                args = call.args[0]
                q_str = str(args).lower()
                if "update examinations" in q_str and "error_message" in q_str:
                    found_update = True

            assert found_update

            # Since we used the progress_tracker.mark_failed, it should have updated the DB
            # Even if my complex check above fails, we check if commit was called at least once after failure
            assert db_mock.commit.called
