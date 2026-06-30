import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import datetime
from app.workers.ai_tasks import ocr_document, cumulative_extraction
async def _ocr_document_async(document_id: str, file_path: str, tenant_id: str):
    return await ocr_document.__wrapped__.__wrapped__(None, document_id, file_path, tenant_id)

async def _cumulative_extraction_async(examination_id: str):
    return await cumulative_extraction.__wrapped__.__wrapped__(None, examination_id)
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.medication import MedicationCatalog


@pytest.mark.asyncio
async def test_ocr_document_async():
    doc_id = uuid4()
    tenant_id = str(uuid4())
    file_path = "/tmp/test.pdf"

    # Mock DB
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_db
    mock_engine = AsyncMock()

    # Mock OCR
    mock_ocr = AsyncMock()
    mock_ocr.extract_text.return_value = "Extracted Text"

    # Mock document in DB
    mock_doc = DocumentModel(
        id=doc_id,
        examination_id=uuid4(),
        tenant_id=tenant_id,
        file_path=file_path,
        extracted_text="Some text",
        include_in_extraction=True,
    )

    # Setup DB mocks for _ocr_document_async
    res_doc = MagicMock()
    res_doc.scalar_one_or_none.return_value = mock_doc

    res_check = MagicMock()
    res_check.scalars.return_value.first.return_value = None  # No processing docs

    res_include = MagicMock()
    res_include.scalars.return_value.all.return_value = [mock_doc]
    res_include.scalars.return_value.first.return_value = mock_doc

    res_lock = MagicMock()
    res_lock.scalar.return_value = True  # pg_try_advisory_xact_lock acquired

    # Execute calls:
    # 1. update status to processing
    # 2. update status to completed
    # 3. select doc
    # 4. pg_try_advisory_xact_lock (audit C3)
    # 5. select check processing
    # 6. select check include
    mock_db.execute.side_effect = [
        MagicMock(),  # update 1
        MagicMock(),  # update 2
        res_doc,  # select doc
        res_lock,  # advisory lock (audit C3)
        res_check,  # check processing
        res_include,  # check include
    ]

    with (
        patch(
            "app.workers.ai_tasks.get_async_session",
            return_value=(mock_db, mock_engine),
        ),
        patch(
            "app.ai.providers.service.AIProviderService.get_ocr_processor",
            AsyncMock(return_value=mock_ocr),
        ),
        patch(
            "app.ai.processors.ocr.utils.convert_to_images",
            AsyncMock(return_value=[MagicMock()]),
        ),
        patch("os.path.exists", return_value=True),
        patch("app.workers.ai_tasks.cumulative_extraction") as mock_cum_task,
    ):
        result = await _ocr_document_async(str(doc_id), file_path, tenant_id)

        assert result["status"] == "completed"
        assert mock_ocr.extract_text.called or mock_ocr.extract_text_from_images.called
        assert mock_cum_task.delay.called


@pytest.mark.asyncio
async def test_cumulative_extraction_async():
    exam_id = uuid4()
    tenant_id = uuid4()

    # Mock DB
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    # begin_nested() is a sync method returning an async context manager.
    # The default AsyncMock returns another AsyncMock which is awaitable
    # but not an async CM, so we wire one explicitly. (Audit C2 made
    # _persist_results wrap its delete + recreate in a SAVEPOINT.)
    _savepoint = AsyncMock()
    _savepoint.__aenter__ = AsyncMock(return_value=_savepoint)
    _savepoint.__aexit__ = AsyncMock(return_value=False)
    mock_db.begin_nested = MagicMock(return_value=_savepoint)
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_db
    mock_engine = AsyncMock()

    # Mock Exam
    mock_exam = ExaminationModel(
        id=exam_id, tenant_id=tenant_id, examination_date=datetime.date.today()
    )

    # Mock Docs
    mock_doc = DocumentModel(
        id=uuid4(), extracted_text="Doc Text", include_in_extraction=True
    )

    # Setup DB results
    res_exam = MagicMock()
    res_exam.scalar_one_or_none.return_value = mock_exam

    res_docs = MagicMock()
    res_docs.scalars.return_value.all.return_value = [mock_doc]

    # result for catalog queries
    res_empty = MagicMock()
    res_empty.scalars.return_value.all.return_value = []

    # Execute calls:
    # 1. update status aggregating
    # 2. select docs
    # 3. select exam
    # 4. select meds (reference)
    # 5. select obs (reference)
    # 6. select biomarkers (catalog)
    # 7. select medications (catalog)
    # 8. update status analyzing_text
    # 9. update status defining_ontology
    # 10. update status persisting_results
    # 11. delete obs
    # 12. delete meds
    # 13. select bio (persistence)
    # 14. select med (persistence)
    # 15. select units
    # 16. update exam final (completed)
    # Create a dynamic resilient mock_execute to handle different query sequences
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        if "from examinations" in q_str:
            return res_exam
        if "from documents" in q_str:
            return res_docs
        if "from fhir_patients" in q_str or "from fhir_observations" in q_str or "from fhir_medications" in q_str or "from biomarker_definitions" in q_str or "from units" in q_str:
            return res_empty
        return MagicMock()

    mock_db.execute.side_effect = mock_execute

    # Mock NLP Extractor
    mock_nlp = AsyncMock()
    mock_parsed = MagicMock()
    mock_parsed.diagnoses = ["Diag 1"]
    mock_parsed.impressions = "Imp 1"
    mock_parsed.known_biomarkers = []
    mock_parsed.unknown_biomarkers = []
    mock_parsed.known_medications = []
    mock_parsed.unknown_medications = []
    mock_nlp.parse_document_pass_1.return_value = mock_parsed

    with (
        patch(
            "app.workers.ai_tasks.get_async_session",
            return_value=(mock_db, mock_engine),
        ),
        patch(
            "app.ai.providers.service.AIProviderService.get_nlp_extractor",
            AsyncMock(return_value=mock_nlp),
        ),
    ):
        result = await _cumulative_extraction_async(str(exam_id))

        assert result["status"] == "completed"
        assert mock_nlp.parse_document_pass_1.called
        # Check that it combined text
        args, kwargs = mock_nlp.parse_document_pass_1.call_args
        assert "Doc Text" in args[0]


@pytest.mark.asyncio
async def test_create_observation_persists_orm_shape_input():
    """The /fhir/* POST endpoints accept ORM-shape dicts (snake_case) — this is
    the app's CRUD contract (frontend speaks ORM-shape). Verifies create_observation
    persists value_quantity / effective_datetime and flattens a FHIR interpretation
    list to the single string the Observation.interpretation column stores.
    """
    from app.services import fhir_service

    class FakeSession:
        def __init__(self):
            self.add = MagicMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    fake_session = FakeSession()
    patient_uuid = str(uuid4())

    with (
        patch.object(fhir_service, "AsyncSessionLocal", return_value=fake_session),
        patch.object(
            fhir_service.NotificationManager, "trigger_event", AsyncMock()
        ),
    ):
        obs = await fhir_service.create_observation(
            {
                "code": {
                    "coding": [{"system": "http://loinc.org", "code": "2345-7"}],
                    "text": "Glucose",
                },
                "subject": {"reference": f"Patient/{patient_uuid}"},
                "value_quantity": {"value": 110, "unit": "mg/dL"},  # ORM-shape
                "effective_datetime": "2026-06-19T08:00:00Z",  # ORM-shape
                "interpretation": [{"coding": [{"display": "High"}]}],  # FHIR list
            },
            tenant_id=str(uuid4()),
        )

    assert obs is not None
    assert obs.effective_datetime is not None
    assert obs.effective_datetime.year == 2026
    assert obs.effective_datetime.day == 19
    assert obs.value_quantity == {"value": 110, "unit": "mg/dL"}
    # raw_value falls back to value_quantity.value
    assert obs.raw_value == 110
    # I6: the canonical FHIR interpretation list is preserved (was collapsed to "High")
    assert obs.interpretation == [{"coding": [{"display": "High"}]}]
