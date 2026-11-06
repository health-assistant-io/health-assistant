"""The ingest funnel: four typed streams, dedup keys, mapping-gap recording.

Pins the plan-8 W2 contract:

* a mixed payload lands in the right streams — observations (+ telemetry split),
  documents, appointments — and an unmapped value is stored **raw** with the
  gap recorded, never dropped silently;
* a re-sync of the same payload writes zero duplicates (natural keys for
  observations/telemetry, ``source_integration_id`` + ``external_id`` for
  documents/examinations);
* unknown telemetry slugs are typed rejections, not free-string rows.

Real Postgres rows are used so the dedup and the canonical-unit/device seams
are exercised rather than mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.document_model import DocumentModel
from app.models.fhir import Observation
from app.models.telemetry_model import TelemetryDataModel
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.services.integration import integration_ingest as funnel


@pytest_asyncio.fixture
async def ctx():
    from app.models.fhir.patient import Patient

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Ingest T.", slug=f"ing-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"ing-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Ingest"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="test_ingest",
                instance_name="ingest-1",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()
        yield integration


def _observation(tenant_id, patient_id, *, code, value, when):
    return Observation(
        tenant_id=tenant_id,
        patient_id=patient_id,
        status="final",
        code=code,
        subject={"reference": f"Patient/{patient_id}"},
        effective_datetime=when,
        value_quantity={"value": value, "unit": "mg/dL"},
    )


def _mappable_observation(integration):
    return _observation(
        integration.tenant_id,
        integration.patient_id,
        code={
            "coding": [{"system": "http://loinc.org", "code": "2093-3"}],
            "text": "Cholesterol",
        },
        value=180.0,
        when=datetime(2026, 10, 20, 9, 0, tzinfo=UTC),
    )


def _unmapped_observation(integration):
    """No LOINC code and no text → no catalog mapping (the gap case)."""
    return _observation(
        integration.tenant_id,
        integration.patient_id,
        code={"coding": [{"system": "urn:local-lab", "code": "GAP-1"}]},
        value=7.0,
        when=datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_mixed_payload_lands_in_the_right_streams_and_records_the_gap(ctx):
    from app.extensions.sdk.documents import DocumentPull

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == ctx.id))
        ).scalar_one()
        from app.services.integration.integration_actor import resolve_integration_actor

        actor = await resolve_integration_actor(db, integration)

        report = await funnel.ingest_observations(
            db,
            integration,
            [_mappable_observation(integration), _unmapped_observation(integration)],
            provider_name=integration.provider,
            instance_name=integration.instance_name,
        )

        assert report.received == 2
        assert report.written == 2, "the unmapped value must still be stored (raw)"
        assert report.written_by_stream.get("observations") == 2
        assert len(report.mapping_gaps) == 1
        assert report.mapping_gaps[0].code == "GAP-1"
        assert report.status == "partial"

        doc_report = await funnel.ingest_documents(
            db,
            integration,
            [
                DocumentPull(
                    filename="lab-report.txt",
                    content=b"mixed-payload document",
                    content_type="text/plain",
                    include_in_extraction=False,
                    external_id="doc-1",
                )
            ],
            actor=actor,
        )
        assert doc_report.written == 1
        assert doc_report.written_by_stream.get("documents") == 1
        await db.commit()

    async with AsyncSessionLocal() as db:
        observations = (
            (await db.execute(select(Observation).where(Observation.tenant_id == ctx.tenant_id)))
            .scalars()
            .all()
        )
        assert len(observations) == 2
        raw = [o for o in observations if o.biomarker_id is None]
        assert len(raw) == 1, "the unmapped observation must be stored raw"
        assert raw[0].code["coding"][0]["code"] == "GAP-1"

        documents = (
            (
                await db.execute(
                    select(DocumentModel).where(DocumentModel.tenant_id == ctx.tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(documents) == 1
        assert documents[0].source_integration_id == ctx.id
        assert documents[0].external_id == "doc-1"


@pytest.mark.asyncio
async def test_resync_with_the_same_cursor_writes_zero_duplicates(ctx):
    from app.extensions.sdk.documents import DocumentPull

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == ctx.id))
        ).scalar_one()
        from app.services.integration.integration_actor import resolve_integration_actor

        actor = await resolve_integration_actor(db, integration)
        payload = [_mappable_observation(integration), _unmapped_observation(integration)]

        first = await funnel.ingest_observations(db, integration, payload)
        doc_payload = [
            DocumentPull(
                filename="repeat.txt",
                content=b"same bytes",
                content_type="text/plain",
                include_in_extraction=False,
                external_id="doc-repeat",
            )
        ]
        first_docs = await funnel.ingest_documents(db, integration, doc_payload, actor=actor)
        await db.commit()

        second = await funnel.ingest_observations(
            db,
            integration,
            [_mappable_observation(integration), _unmapped_observation(integration)],
        )
        second_docs = await funnel.ingest_documents(db, integration, doc_payload, actor=actor)
        await db.commit()

        assert first.written == 2 and first.deduped == 0
        assert second.written == 0, "a re-sync with the same cursor writes zero rows"
        assert second.deduped == 2
        assert first_docs.written == 1
        assert second_docs.written == 0 and second_docs.deduped == 1

    async with AsyncSessionLocal() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Observation)
                .where(Observation.tenant_id == ctx.tenant_id)
            )
        ).scalar_one()
        assert count == 2
        doc_count = (
            await db.execute(
                select(func.count())
                .select_from(DocumentModel)
                .where(DocumentModel.tenant_id == ctx.tenant_id)
            )
        ).scalar_one()
        assert doc_count == 1


@pytest.mark.asyncio
async def test_telemetry_stream_uses_the_device_and_canonical_unit_seam(ctx):
    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == ctx.id))
        ).scalar_one()
        # The slug registry requires a declared telemetry biomarker.
        db.add(
            BiomarkerDefinition(
                id=uuid.uuid4(),
                slug="heart-rate",
                coding_system="loinc",
                code="8867-4",
                name="Heart rate",
                is_telemetry=True,
                tenant_id=None,
            )
        )
        await db.commit()

        readings = [
            funnel.TelemetryReading(
                slug="heart-rate",
                value=72.0,
                unit="bpm",
                timestamp=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
                device_external_ref="watch-1",
            )
        ]
        report = await funnel.ingest_telemetry(db, integration, readings)
        await db.commit()
        assert report.written == 1
        assert report.written_by_stream.get("telemetry") == 1

        # Same reading again → deduped on (patient, device, slug, timestamp).
        again = await funnel.ingest_telemetry(db, integration, readings)
        await db.commit()
        assert again.written == 0 and again.deduped == 1

        unknown = await funnel.ingest_telemetry(
            db,
            integration,
            [
                funnel.TelemetryReading(
                    slug="not-a-metric",
                    value=1.0,
                    timestamp=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
                )
            ],
        )
        assert unknown.written == 0
        assert unknown.rejected == 1
        assert "unknown telemetry" in unknown.rejections[0]["reason"].lower()

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(TelemetryDataModel).where(TelemetryDataModel.tenant_id == ctx.tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.slug == "heart-rate"
        assert row.value == 72.0
        assert row.raw_value == 72.0
        assert row.patient_id == ctx.patient_id
        from app.models.telemetry_model import TelemetryDeviceModel

        device = (
            await db.execute(
                select(TelemetryDeviceModel).where(TelemetryDeviceModel.id == row.device_id)
            )
        ).scalar_one()
        assert device.external_ref == "watch-1"
        assert device.source == "test_ingest"


@pytest.mark.asyncio
async def test_appointments_dedup_on_the_source_key(ctx):
    from app.schemas.examination import ExaminationCreate

    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == ctx.id))
        ).scalar_one()
        from app.services.integration.integration_actor import resolve_integration_actor

        actor = await resolve_integration_actor(db, integration)
        payload = ExaminationCreate(
            patient_id=integration.patient_id,
            examination_date=datetime(2026, 10, 20, tzinfo=UTC).date(),
            notes="Appointment from the funnel test",
            auto_extract_metadata=False,
            extraction_status="completed",
        )
        first, exam_map = await funnel.ingest_appointments(
            db, integration, examinations=[payload], actor=actor
        )
        await db.commit()
        assert first.written == 1
        assert first.written_by_stream.get("appointments") == 1

        # Provider sets the upstream id on re-pull: dedup at the source key.
        payload.external_id = "exam-1"
        first_with_id, exam_map = await funnel.ingest_appointments(
            db, integration, examinations=[payload], actor=actor
        )
        await db.commit()
        assert first_with_id.written == 1

        again, _ = await funnel.ingest_appointments(
            db, integration, examinations=[payload], actor=actor
        )
        await db.commit()
        assert again.written == 0
        assert again.deduped == 1
        assert exam_map, "the exam external→id map feeds document linking"


def test_funnel_is_the_declared_one_path():
    """Regression pin: the engine + bridge route through this module."""
    import inspect

    from app.extensions.builtin.health_assistant_bridge import provider as bridge
    from app.services import integration_sync_service as svc
    from app.services.integration import sync_legs

    run_src = inspect.getsource(svc.run_sync) + inspect.getsource(sync_legs)
    assert "ingest_observations" in run_src
    assert "ingest_documents" in run_src
    assert "ingest_appointments" in run_src

    bridge_src = inspect.getsource(bridge)
    assert "ingest_observations" in bridge_src
    assert "apply_telemetry_split" not in bridge_src
