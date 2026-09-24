"""Patient contact fields survive the REST create/update path.

``create_patient`` / ``update_patient`` copy known keys from an untyped dict
onto the ORM row and then run the FHIR write gate (``assert_valid_fhir``).
``emergency_contact`` is not part of the FHIR resource but must still be
persisted; ``Address.line`` must be a list or the gate rejects the write.
"""

import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.tenant_model import TenantModel
from app.services import fhir_service
from app.services.fhir_helpers import FhirSerializationError


@pytest_asyncio.fixture
async def tenant_id():
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="Patient Contacts T.",
                slug=f"contacts-{tenant_id.hex[:8]}",
            )
        )
        await db.commit()
    return tenant_id


def _patient_payload(**overrides):
    payload = {
        "name": [{"family": "Contact", "given": ["Test"]}],
        "gender": "female",
        "address": [{"line": ["1 Main St", "Apt 2"], "city": "Athens"}],
        "telecom": [{"system": "phone", "value": "+30 555", "use": "mobile"}],
        "emergency_contact": {"name": "Jane", "relationship": "sister", "phone": "+30 556"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_persists_contacts(tenant_id):
    created = await fhir_service.create_patient(_patient_payload(), tenant_id)

    stored = await fhir_service.get_patient(created.id)
    assert stored.address == [{"line": ["1 Main St", "Apt 2"], "city": "Athens"}]
    assert stored.telecom == [{"system": "phone", "value": "+30 555", "use": "mobile"}]
    assert stored.emergency_contact == {
        "name": "Jane",
        "relationship": "sister",
        "phone": "+30 556",
    }


@pytest.mark.asyncio
async def test_update_sets_keeps_and_clears_emergency_contact(tenant_id):
    created = await fhir_service.create_patient(
        _patient_payload(emergency_contact=None), tenant_id
    )

    await fhir_service.update_patient(
        created.id, {"emergency_contact": {"name": "Joe", "phone": "+30 557"}}
    )
    assert (await fhir_service.get_patient(created.id)).emergency_contact == {
        "name": "Joe",
        "phone": "+30 557",
    }

    await fhir_service.update_patient(created.id, {"gender": "male"})
    assert (await fhir_service.get_patient(created.id)).emergency_contact == {
        "name": "Joe",
        "phone": "+30 557",
    }

    await fhir_service.update_patient(created.id, {"emergency_contact": None})
    assert (await fhir_service.get_patient(created.id)).emergency_contact is None


@pytest.mark.asyncio
async def test_update_rejects_string_address_line(tenant_id):
    created = await fhir_service.create_patient(_patient_payload(), tenant_id)

    with pytest.raises(FhirSerializationError):
        await fhir_service.update_patient(
            created.id, {"address": [{"line": "1 Main St", "city": "Athens"}]}
        )

    stored = await fhir_service.get_patient(created.id)
    assert stored.address == [{"line": ["1 Main St", "Apt 2"], "city": "Athens"}]
