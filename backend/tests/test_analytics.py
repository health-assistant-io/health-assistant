import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import uuid
import datetime


@pytest.mark.asyncio
async def test_get_biomarker_trends():
    from app.services.analytics_service import get_biomarker_trends
    from app.models.fhir.patient import Observation

    tenant_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    # Mock Observation
    mock_obs = MagicMock()
    mock_obs.document_id = doc_id
    mock_obs.examination_id = None
    mock_obs.code = {"text": "Glucose"}
    mock_obs.value_quantity = {"value": 100, "unit": "mg/dL"}
    mock_obs.effective_datetime = datetime.datetime(2023, 1, 1)
    mock_obs.relative_score = 0.5
    mock_obs.normalized_value = 100

    # Mock DB Session
    class TrendsMockResult:
        def __init__(self, data):
            self.data = data

        def scalars(self):
            class MockScalars:
                def all(self_inner):
                    return self.data

            return MockScalars()

        def all(self):
            return self.data

    async def trends_mock_execute(query):
        # Determine which query this is by string representation
        query_str = str(query)
        if "fhir_observations" in query_str:
            return TrendsMockResult([mock_obs])
        elif "documents" in query_str and "examinations" in query_str:
            # Document to Examination join query
            exam_id = uuid.uuid4()
            exam_date = datetime.date(2023, 5, 5)
            exam_category = "Laboratory Tests"
            # Return 5 columns as expected by service: doc_id, exam_id, exam_date, exam_category, entities
            return TrendsMockResult([(doc_id, exam_id, exam_date, exam_category, {})])
        return TrendsMockResult([])

    db_mock = AsyncMock()
    db_mock.execute = trends_mock_execute

    # Test getting trends
    trends = await get_biomarker_trends(tenant_id=tenant_id, db=db_mock)

    assert "biomarkers" in trends
    assert "glucose" in trends["biomarkers"]

    # The date should be mapped to the examination date (2023-05-05) not the original effective_datetime
    glucose_data = trends["biomarkers"]["glucose"][0]
    assert glucose_data["value"] == 100
    assert glucose_data["unit"] == "mg/dL"
    assert "2023-05-05" in glucose_data["date"]


@pytest.mark.asyncio
async def test_get_dashboard_data():
    from app.services.analytics_service import get_dashboard_data

    tenant_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    exam_id = str(uuid.uuid4())

    # Mock Document
    mock_doc = MagicMock()
    mock_doc.id = doc_id
    mock_doc.filename = "test.pdf"
    mock_doc.updated_at = datetime.datetime(2023, 1, 1)
    mock_doc.examination_id = exam_id

    # Mock Examination
    mock_exam = MagicMock()
    mock_exam.id = exam_id
    mock_exam.examination_date = datetime.date(2023, 5, 5)

    # Mock DB Session
    class DashMockResult:
        def __init__(self, data):
            self.data = data

        def scalars(self):
            class MockScalars:
                def all(self_inner):
                    return self.data

            return MockScalars()

        def scalar_one_or_none(self):
            return self.data[0] if self.data else None

        def all(self):
            return self.data

    async def dash_mock_execute(query):
        query_str = str(query).lower()
        # print(f"DEBUG QUERY: {query_str}")
        if "documents" in query_str:
            if "join" in query_str:
                return DashMockResult([(mock_doc, "Imaging")])
            return DashMockResult([mock_doc])
        elif "examinations" in query_str:
            return DashMockResult([mock_exam])
        return DashMockResult([])

    db_mock = AsyncMock()
    db_mock.execute = dash_mock_execute

    dashboard = await get_dashboard_data(tenant_id=tenant_id, db=db_mock)

    assert "recent_documents" in dashboard
    assert len(dashboard["recent_documents"]) == 1
    assert "latest_examination" in dashboard
    assert "latest_imaging" in dashboard
    assert "latest_labs" in dashboard

    # The created_at field should reflect the examination date, not document.updated_at
    doc_data = dashboard["recent_documents"][0]
    assert "2023-05-05" in doc_data["created_at"]
