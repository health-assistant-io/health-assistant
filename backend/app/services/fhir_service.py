from typing import Dict, Any, Optional, Union
from uuid import UUID
import logging
import datetime
from datetime import date, datetime as dt
from sqlalchemy import select, func
from app.models.fhir import Patient, Observation, DiagnosticReport, Medication
from app.services.notification_manager import NotificationManager
from app.models.notification import NotificationType, TriggerType
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE

logger = logging.getLogger(__name__)


def _parse_date(d):
    """Internal helper to parse date strings from FHIR-like dicts"""
    if not d:
        return None
    if isinstance(d, (date, dt)):
        return d if isinstance(d, date) else d.date()
    try:
        return date.fromisoformat(d.split("T")[0])
    except (ValueError, TypeError):
        return None


def _parse_datetime(d):
    """Internal helper to parse datetime strings from FHIR-like dicts"""
    if not d:
        return None
    if isinstance(d, dt):
        return d
    if isinstance(d, date):
        return dt.combine(d, dt.min.time())
    try:
        return dt.fromisoformat(d.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def create_patient(
    patient_data: dict, tenant_id: str | UUID
) -> Optional[Patient]:
    """Create a new patient"""
    if not DATABASE_AVAILABLE:
        return None

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        return None

    from app.models.enums import Gender

    # Parse birth_date safely
    raw_birth_date = patient_data.get("birth_date") or patient_data.get("birthDate")
    parsed_birth_date = _parse_date(raw_birth_date)

    # Parse gender safely into enum
    gender_str = patient_data.get("gender", "unknown").upper()
    try:
        gender_enum = getattr(Gender, gender_str)
    except AttributeError:
        gender_enum = Gender.UNKNOWN

    new_patient = Patient(
        tenant_id=tenant_id,
        user_id=patient_data.get("user_id"),
        name=patient_data.get("name", {}),
        gender=gender_enum,
        birth_date=parsed_birth_date,
        mrn=patient_data.get("mrn"),
        address=patient_data.get("address"),
        telecom=patient_data.get("telecom"),
    )

    try:
        async with AsyncSessionLocal() as session:
            session.add(new_patient)
            await session.commit()
            await session.refresh(new_patient)

        return new_patient
    except Exception as e:
        logger.error(f"Failed to create patient: {str(e)}")
        raise


async def get_patient(
    patient_id: str | UUID, tenant_id: Optional[str | UUID] = None
) -> Optional[Patient]:
    """Get patient by ID, optionally filtered by tenant"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(patient_id, str):
        try:
            patient_id = UUID(patient_id)
        except ValueError:
            return None

    if tenant_id and isinstance(tenant_id, str):
        try:
            tenant_id = UUID(tenant_id)
        except ValueError:
            pass

    async with AsyncSessionLocal() as session:
        query = select(Patient).where(Patient.id == patient_id)
        if tenant_id:
            query = query.where(Patient.tenant_id == tenant_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()


async def update_patient_layout(
    patient_id: str | UUID, layout: Dict[str, Any]
) -> Optional[Patient]:
    """Update patient dashboard layout"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(patient_id, str):
        try:
            patient_id = UUID(patient_id)
        except ValueError:
            return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Patient).where(Patient.id == patient_id))
        patient = result.scalar_one_or_none()
        if patient:
            patient.dashboard_layout = layout
            await session.commit()
            await session.refresh(patient)
        return patient


async def update_patient(
    patient_id: str | UUID, patient_data: dict
) -> Optional[Patient]:
    """Update patient information"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(patient_id, str):
        try:
            patient_id = UUID(patient_id)
        except ValueError:
            return None

    import datetime
    from app.models.enums import Gender

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Patient).where(Patient.id == patient_id))
        patient = result.scalar_one_or_none()
        if patient:
            if "name" in patient_data:
                patient.name = patient_data["name"]

            if "user_id" in patient_data:
                patient.user_id = patient_data["user_id"]

            if "gender" in patient_data:
                gender_str = patient_data["gender"].upper()
                try:
                    patient.gender = getattr(Gender, gender_str)
                except AttributeError:
                    patient.gender = Gender.UNKNOWN

            if "birth_date" in patient_data or "birthDate" in patient_data:
                raw_birth_date = patient_data.get("birth_date") or patient_data.get(
                    "birthDate"
                )
                if isinstance(raw_birth_date, str):
                    try:
                        patient.birth_date = datetime.datetime.strptime(
                            raw_birth_date, "%Y-%m-%d"
                        ).date()
                    except ValueError:
                        pass
                else:
                    patient.birth_date = raw_birth_date

            if "mrn" in patient_data:
                patient.mrn = patient_data["mrn"]

            if "address" in patient_data:
                patient.address = patient_data["address"]

            if "telecom" in patient_data:
                patient.telecom = patient_data["telecom"]

            await session.commit()
            await session.refresh(patient)
        return patient


async def delete_patient(patient_id: str | UUID) -> bool:
    """Delete patient and all associated clinical data"""
    if not DATABASE_AVAILABLE:
        return False

    if isinstance(patient_id, str):
        try:
            patient_id = UUID(patient_id)
        except ValueError:
            return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Patient).where(Patient.id == patient_id))
        patient = result.scalar_one_or_none()
        if patient:
            # Note: Cascade delete should handle documents, observations etc if configured in models
            # Otherwise we'd need manual cleanup here
            await session.delete(patient)
            await session.commit()
            return True
        return False


async def list_patients(
    tenant_id: str | UUID | None,
    limit: int = 10,
    offset: int = 0,
    user_id: Optional[str | UUID] = None,
) -> Dict[str, Any]:
    """List patients (with pagination and filtering)"""
    if not DATABASE_AVAILABLE:
        return {"items": [], "total": 0}

    try:
        if tenant_id and isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
        if user_id and isinstance(user_id, str):
            user_id = UUID(user_id)
    except ValueError:
        return {"items": [], "total": 0}

    async with AsyncSessionLocal() as session:
        query = select(Patient)
        if tenant_id:
            query = query.where(Patient.tenant_id == tenant_id)

        if user_id:
            query = query.where(Patient.user_id == user_id)

        # Total count
        count_query = select(func.count(Patient.id))
        if tenant_id:
            count_query = count_query.where(Patient.tenant_id == tenant_id)
        if user_id:
            count_query = count_query.where(Patient.user_id == user_id)

        total = await session.execute(count_query)
        total_count = total.scalar() or 0

        # Pagination
        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        items = result.scalars().unique().all()

        patient_list = []
        for item in items:
            try:
                if hasattr(item, "to_dict"):
                    patient_list.append(item.to_dict())
                else:
                    patient_list.append(str(item))
            except Exception as e:
                logger.error(
                    f"Error serializing patient {getattr(item, 'id', 'unknown')}: {e}"
                )
                # Fallback to a very basic dict if to_dict fails
                patient_list.append(
                    {
                        "id": str(getattr(item, "id", "")),
                        "error": "Serialization failed",
                    }
                )

        return {
            "items": patient_list,
            "total": total_count,
        }


async def create_observation(
    observation_data: dict, tenant_id: str | UUID
) -> Optional[Observation]:
    """Create a new observation"""
    if not DATABASE_AVAILABLE:
        return None

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        return None

    # Handle both camelCase (FHIR standard) and snake_case (internal)
    value_quantity = observation_data.get("value_quantity") or observation_data.get("valueQuantity")
    interpretation = observation_data.get("interpretation")
    
    # Map FHIR-style interpretation list to a single string if needed
    interpretation_str = None
    if isinstance(interpretation, list) and len(interpretation) > 0:
        interp_obj = interpretation[0]
        if isinstance(interp_obj, dict):
            coding = interp_obj.get("coding", [])
            if len(coding) > 0:
                interpretation_str = coding[0].get("display") or coding[0].get("code")
    elif isinstance(interpretation, str):
        interpretation_str = interpretation

    new_obs = Observation(
        tenant_id=tenant_id,
        status=observation_data.get("status", "final"),
        code=observation_data.get("code", {}),
        subject=observation_data.get("subject", {}),
        value_quantity=value_quantity,
        effective_datetime=_parse_datetime(observation_data.get("effective_datetime")),
        examination_id=observation_data.get("examination_id"),
        biomarker_id=observation_data.get("biomarker_id"),
        interpretation=interpretation_str,
        raw_value=observation_data.get("raw_value") or (value_quantity.get("value") if value_quantity else None),
        document_id=observation_data.get("document_id")
    )

    async with AsyncSessionLocal() as session:
        session.add(new_obs)
        await session.commit()
        await session.refresh(new_obs)

    # Check for biomarker thresholds (Biomarker Alert)
    # This would normally query the BiomarkerDefinition for thresholds
    # For now, let's trigger a generic event that the NotificationManager can catch
    patient_id = None
    subject = observation_data.get("subject", {})
    if subject and "reference" in subject:
        ref = subject["reference"]
        if "Patient/" in ref:
            try:
                patient_id = UUID(ref.split("/")[-1])
            except ValueError:
                pass

    if patient_id:
        await NotificationManager.trigger_event(
            event_name="biomarker_update",
            patient_id=patient_id,
            tenant_id=tenant_id,
            data={
                "observation_id": str(new_obs.id),
                "code": new_obs.code,
                "value": new_obs.value_quantity,
            },
        )

    return new_obs


async def get_observation(observation_id: str | UUID) -> Optional[Observation]:
    """Get observation by ID"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(observation_id, str):
        try:
            observation_id = UUID(observation_id)
        except ValueError:
            return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Observation).where(Observation.id == observation_id)
        )
        return result.scalar_one_or_none()


async def delete_observation(observation_id: str | UUID) -> bool:
    """Delete observation by ID"""
    if not DATABASE_AVAILABLE:
        return False

    if isinstance(observation_id, str):
        try:
            observation_id = UUID(observation_id)
        except ValueError:
            return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Observation).where(Observation.id == observation_id)
        )
        observation = result.scalar_one_or_none()
        if observation:
            await session.delete(observation)
            await session.commit()
            return True
        return False


async def list_observations(
    tenant_id: str | UUID,
    patient_id: str | UUID = None,
    code: str = None,
    start_date: str = None,
    end_date: str = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """List observations (with filtering and pagination)"""
    if not DATABASE_AVAILABLE:
        return {"items": [], "total": 0}

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        return {"items": [], "total": 0}

    async with AsyncSessionLocal() as session:
        query = select(Observation).where(Observation.tenant_id == tenant_id)

        # Add filtering here if needed (subject reference parsing might be complex)

        # Total count - simpler query
        count_query = select(func.count(Observation.id)).where(
            Observation.tenant_id == tenant_id
        )
        total = await session.execute(count_query)
        total_count = total.scalar() or 0

        # Pagination
        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        items = result.scalars().all()

        obs_list = []
        for item in items:
            try:
                if hasattr(item, "to_dict"):
                    obs_list.append(item.to_dict())
                else:
                    obs_list.append(str(item))
            except Exception as e:
                logger.error(
                    f"Error serializing observation {getattr(item, 'id', 'unknown')}: {e}"
                )
                obs_list.append(
                    {
                        "id": str(getattr(item, "id", "")),
                        "error": "Serialization failed",
                    }
                )

        return {
            "items": obs_list,
            "total": total_count,
        }


async def create_diagnostic_report(
    report_data: dict, tenant_id: str | UUID
) -> Optional[DiagnosticReport]:
    """Create a new diagnostic report"""
    if not DATABASE_AVAILABLE:
        return None

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        return None

    new_report = DiagnosticReport(
        tenant_id=tenant_id,
        status=report_data.get("status", "final"),
        code=report_data.get("code", {}),
        subject=report_data.get("subject", {}),
        conclusion=report_data.get("conclusion"),
        effective_datetime=_parse_datetime(report_data.get("effective_datetime")),
    )

    async with AsyncSessionLocal() as session:
        session.add(new_report)
        await session.commit()
        await session.refresh(new_report)

    return new_report


async def get_diagnostic_report(report_id: str | UUID) -> Optional[DiagnosticReport]:
    """Get diagnostic report by ID"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(report_id, str):
        try:
            report_id = UUID(report_id)
        except ValueError:
            return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiagnosticReport).where(DiagnosticReport.id == report_id)
        )
        return result.scalar_one_or_none()


async def create_medication(
    medication_data: dict, tenant_id: str | UUID
) -> Optional[Medication]:
    """Create a new medication"""
    if not DATABASE_AVAILABLE:
        return None

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        return None

    # Extract patient_id from subject if possible for the new model
    patient_id = None
    subject = medication_data.get("subject", {})
    if subject and "reference" in subject:
        ref = subject["reference"]
        if "Patient/" in ref:
            try:
                patient_id = UUID(ref.split("/")[-1])
            except ValueError:
                pass

    if not patient_id and "patient_id" in medication_data:
        try:
            patient_id = UUID(str(medication_data["patient_id"]))
        except ValueError:
            pass

    if not patient_id:
        logger.error("Cannot create medication: No patient_id found")
        return None

    new_med = Medication(
        tenant_id=tenant_id,
        patient_id=patient_id,
        code=medication_data.get("code", {}),
        status=medication_data.get("status", "ACTIVE"),
        subject=subject,
        start_date=_parse_date(medication_data.get("start_date")),
        end_date=_parse_date(medication_data.get("end_date")),
        frequency=medication_data.get("timing") or medication_data.get("frequency"),
    )

    async with AsyncSessionLocal() as session:
        session.add(new_med)
        await session.commit()
        await session.refresh(new_med)

    # Automatically create medication reminders if timing is specified
    # FHIR Timing structure: https://www.hl7.org/fhir/datatypes.html#Timing
    timing = medication_data.get("timing", {})
    repeat = timing.get("repeat", {})

    # Handle specific times (FHIR timeOfDay)
    times_of_day = repeat.get("timeOfDay", [])
    if not isinstance(times_of_day, list):
        times_of_day = [times_of_day]

    # Handle days of week (FHIR dayOfWeek)
    days_of_week = repeat.get("dayOfWeek", [])
    if not isinstance(days_of_week, list):
        days_of_week = [days_of_week]

    if patient_id and timing:
        await NotificationManager.sync_medication_triggers(
            patient_id=patient_id,
            medication_id=new_med.id,
            medication_name=new_med.code.get("text", "medication"),
            timing_data=timing,
            tenant_id=tenant_id,
        )

    return new_med


async def get_medication(medication_id: str | UUID) -> Optional[Medication]:
    """Get medication by ID"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(medication_id, str):
        try:
            medication_id = UUID(medication_id)
        except ValueError:
            return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Medication).where(Medication.id == medication_id)
        )
        return result.scalar_one_or_none()


async def list_medications(
    tenant_id: str | UUID,
    patient_id: str | UUID = None,
    status: str = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """List medications (with filtering and pagination)"""
    if not DATABASE_AVAILABLE:
        return {"items": [], "total": 0}

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        return {"items": [], "total": 0}

    async with AsyncSessionLocal() as session:
        query = select(Medication).where(Medication.tenant_id == tenant_id)

        # Total count - simpler query
        count_query = select(func.count(Medication.id)).where(
            Medication.tenant_id == tenant_id
        )
        total = await session.execute(count_query)
        total_count = total.scalar() or 0

        # Pagination
        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        items = result.scalars().all()

        med_list = []
        for item in items:
            try:
                if hasattr(item, "to_dict"):
                    med_list.append(item.to_dict())
                else:
                    med_list.append(str(item))
            except Exception as e:
                logger.error(
                    f"Error serializing medication {getattr(item, 'id', 'unknown')}: {e}"
                )
                med_list.append(
                    {
                        "id": str(getattr(item, "id", "")),
                        "error": "Serialization failed",
                    }
                )

        return {
            "items": med_list,
            "total": total_count,
        }


async def map_observations_to_biomarkers(db, observations):
    """
    Map raw FHIR observations from integrations to BiomarkerDefinitions.
    Creates new definitions if they do not exist.
    """
    from app.models.biomarker_model import BiomarkerDefinition
    import re
    import logging

    logger = logging.getLogger(__name__)

    for obs in observations:
        if not obs.biomarker_id and obs.code:
            loinc_code = next(
                (
                    c.get("code")
                    for c in obs.code.get("coding", [])
                    if "loinc.org" in c.get("system", "")
                ),
                None,
            )
            text = obs.code.get("text")

            bdef = None
            if loinc_code:
                res = await db.execute(
                    select(BiomarkerDefinition).where(
                        BiomarkerDefinition.code == loinc_code
                    )
                )
                bdef = res.scalars().first()

            if not bdef and text:
                res = await db.execute(
                    select(BiomarkerDefinition).where(
                        BiomarkerDefinition.name.ilike(text)
                    )
                )
                bdef = res.scalars().first()

            if not bdef and text:
                slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
                res = await db.execute(
                    select(BiomarkerDefinition).where(BiomarkerDefinition.slug == slug)
                )
                bdef = res.scalars().first()

                if not bdef:
                    bdef = BiomarkerDefinition(
                        slug=slug,
                        coding_system="loinc" if loinc_code else "custom",
                        code=loinc_code,
                        name=text,
                        category="vital_signs"
                        if "rate" in text.lower() or "pressure" in text.lower()
                        else "other",
                        tenant_id=obs.tenant_id,
                    )
                    db.add(bdef)
                    await db.flush()
                    logger.info(f"Auto-created catalog entry for {text} (slug: {slug})")

            if bdef:
                obs.biomarker_id = bdef.id
