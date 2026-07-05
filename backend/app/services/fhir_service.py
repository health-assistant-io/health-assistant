from typing import Dict, Any, Optional, List
from uuid import UUID
import logging
from datetime import date, datetime as dt
from sqlalchemy import select, func, and_
from app.models.fhir import Patient, Observation, DiagnosticReport, Medication
from app.services.fhir_helpers import _extract_patient_id, _normalize_interpretation, assert_valid_fhir, validate_and_filter_observations
from app.services.notification_manager import NotificationManager
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
    from datetime import timezone
    if not d:
        return None
    if isinstance(d, dt):
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    if isinstance(d, date):
        return dt.combine(d, dt.min.time(), tzinfo=timezone.utc)
    try:
        parsed = dt.fromisoformat(d.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
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

    # The /fhir/* POST endpoints accept ORM-shape dicts (snake_case). Coerce
    # types directly — no FHIR conversion is involved (input is already the
    # target shape). See plan: REST CRUD path is separated from FHIR parsing.
    gender_str = (patient_data.get("gender") or "unknown").upper()
    try:
        gender_enum = getattr(Gender, gender_str)
    except AttributeError:
        gender_enum = Gender.UNKNOWN

    mrn = (patient_data.get("mrn") or "").strip() or None

    new_patient = Patient(
        tenant_id=tenant_id,
        user_id=patient_data.get("user_id"),
        name=patient_data.get("name") or {},
        gender=gender_enum,
        birth_date=_parse_date(patient_data.get("birth_date")),
        mrn=mrn,
        address=patient_data.get("address"),
        telecom=patient_data.get("telecom"),
    )
    assert_valid_fhir(new_patient)

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

    from app.models.enums import Gender

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Patient).where(Patient.id == patient_id))
        patient = result.scalar_one_or_none()
        if patient:
            # ORM-shape input — coerce types directly (no FHIR conversion).
            if "name" in patient_data:
                patient.name = patient_data["name"] or {}

            if "user_id" in patient_data:
                patient.user_id = patient_data["user_id"]

            if "gender" in patient_data:
                gender_str = (patient_data["gender"] or "unknown").upper()
                try:
                    patient.gender = getattr(Gender, gender_str)
                except AttributeError:
                    patient.gender = Gender.UNKNOWN

            if "birth_date" in patient_data:
                patient.birth_date = _parse_date(patient_data["birth_date"])

            if "mrn" in patient_data:
                patient.mrn = (patient_data["mrn"] or "").strip() or None

            if "address" in patient_data:
                patient.address = patient_data["address"]

            if "telecom" in patient_data:
                patient.telecom = patient_data["telecom"]

            assert_valid_fhir(patient)
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

    # ORM-shape input (snake_case) — coerce types directly. The /fhir/* POST
    # endpoints speak ORM-shape; FHIR parsing lives only at the import boundary.
    value_quantity = observation_data.get("value_quantity")
    subject = observation_data.get("subject") or {}

    new_obs = Observation(
        tenant_id=tenant_id,
        status=observation_data.get("status", "final"),
        code=observation_data.get("code") or {},
        subject=subject,
        value_quantity=value_quantity,
        effective_datetime=_parse_datetime(observation_data.get("effective_datetime")),
        examination_id=observation_data.get("examination_id"),
        biomarker_id=observation_data.get("biomarker_id"),
        interpretation=_normalize_interpretation(observation_data.get("interpretation")),
        raw_value=observation_data.get("raw_value")
        or (value_quantity.get("value") if value_quantity else None),
        document_id=observation_data.get("document_id"),
    )
    assert_valid_fhir(new_obs)

    async with AsyncSessionLocal() as session:
        session.add(new_obs)
        await session.commit()
        await session.refresh(new_obs)

    # Evaluate biomarker rules (event-driven notification source).
    patient_id = None
    patient_id_str = observation_data.get("patient_id") or _extract_patient_id(subject)
    if patient_id_str:
        try:
            patient_id = UUID(str(patient_id_str))
        except (ValueError, TypeError):
            pass

    if patient_id and new_obs.biomarker_id:
        try:
            from app.services.notification_rule_service import evaluate_and_fire

            await evaluate_and_fire(
                observation=new_obs,
                patient_id=patient_id,
                tenant_id=tenant_id,
            )
        except Exception:
            # Rule evaluation must never break observation persistence.
            import logging

            logging.getLogger(__name__).exception(
                "Biomarker rule evaluation failed for observation %s", new_obs.id
            )

    return new_obs


async def get_observation(
    observation_id: str | UUID,
    tenant_id: str | UUID | None = None,
) -> Optional[Observation]:
    """Get observation by ID.

    ``tenant_id`` is an optional parameter; when supplied, the query is
    restricted to that tenant so the service itself enforces isolation
    (defense in depth). ``None`` preserves unscoped behaviour for internal
    callers that have already verified access (e.g. export/import).
    """
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(observation_id, str):
        try:
            observation_id = UUID(observation_id)
        except ValueError:
            return None

    predicates = [Observation.id == observation_id]
    if tenant_id is not None:
        try:
            tid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        except (ValueError, TypeError):
            return None
        predicates.append(Observation.tenant_id == tid)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Observation).where(*predicates))
        return result.scalar_one_or_none()


async def delete_observation(
    observation_id: str | UUID,
    tenant_id: str | UUID | None = None,
) -> bool:
    """Delete observation by ID.

    ``tenant_id`` is an optional parameter; when supplied the lookup is
    tenant-scoped so cross-tenant deletes are impossible even if the
    endpoint forgets to check.
    """
    if not DATABASE_AVAILABLE:
        return False

    if isinstance(observation_id, str):
        try:
            observation_id = UUID(observation_id)
        except ValueError:
            return False

    predicates = [Observation.id == observation_id]
    if tenant_id is not None:
        try:
            tid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        except (ValueError, TypeError):
            return False
        predicates.append(Observation.tenant_id == tid)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Observation).where(*predicates)
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
    """List observations (with filtering and pagination).

    All four filters (``patient_id``/``code``/``start_date``/``end_date``)
    are applied to the query, plus the tenant scope. Filters are AND-combined.
    """
    if not DATABASE_AVAILABLE:
        return {"items": [], "total": 0}

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        return {"items": [], "total": 0}

    # Build predicate list — always tenant-scoped, plus optional filters.
    predicates = [Observation.tenant_id == tenant_id]

    subject_ref_patient_id: Optional[UUID] = None
    if patient_id is not None:
        try:
            subject_ref_patient_id = (
                UUID(patient_id) if isinstance(patient_id, str) else patient_id
            )
        except ValueError:
            return {"items": [], "total": 0}
        # The FHIR ``subject`` JSONB looks like {"reference": "Patient/<uuid>"}.
        # Match it via the text cast so we use the index-friendly path.
        predicates.append(
            Observation.subject["reference"].astext
            == f"Patient/{subject_ref_patient_id}"
        )

    if code:
        # The FHIR ``code`` JSONB contains a coding list:
        # {"coding": [{"system": "http://loinc.org", "code": "8867-4"}], ...}.
        # Match by JSON path. ``code`` here is the LOINC/OID code string.
        predicates.append(
            Observation.code["coding"][0]["code"].astext == str(code)
        )

    if start_date:
        start_dt = _parse_date(start_date)
        if start_dt is not None:
            predicates.append(Observation.effective_datetime >= start_dt)

    if end_date:
        end_dt = _parse_date(end_date)
        if end_dt is not None:
            predicates.append(Observation.effective_datetime <= end_dt)

    combined = and_(*predicates)

    async with AsyncSessionLocal() as session:
        count_query = select(func.count(Observation.id)).where(combined)
        total = await session.execute(count_query)
        total_count = total.scalar() or 0

        query = (
            select(Observation)
            .where(combined)
            .order_by(Observation.effective_datetime.desc().nullslast())
            .limit(limit)
            .offset(offset)
        )
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


async def get_observation_history(
    tenant_id: str | UUID,
    patient_id: str | UUID,
    code: str,
    period: str = "last-6-months",
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Get observation history for a single patient+code pair.

    Dedicated lookup used by the ``/fhir/Observation/history`` endpoint.
    Tenant-scoped via the ``tenant_id`` parameter.
    """
    if not DATABASE_AVAILABLE:
        return []

    try:
        tenant_uuid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
    except (ValueError, TypeError):
        return []

    try:
        patient_uuid = (
            UUID(patient_id) if isinstance(patient_id, str) else patient_id
        )
    except (ValueError, TypeError):
        return []

    # Map the human-readable period token to a date cutoff.
    period_days = {
        "last-30-days": 30,
        "last-3-months": 90,
        "last-6-months": 180,
        "last-year": 365,
        "all": 3650,
    }.get(period, 180)

    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    combined = and_(
        Observation.tenant_id == tenant_uuid,
        Observation.subject["reference"].astext == f"Patient/{patient_uuid}",
        Observation.code["coding"][0]["code"].astext == str(code),
        Observation.effective_datetime >= cutoff,
    )

    async with AsyncSessionLocal() as session:
        query = (
            select(Observation)
            .where(combined)
            .order_by(Observation.effective_datetime.asc())
            .limit(limit)
        )
        result = await session.execute(query)
        items = result.scalars().all()

        out: List[Dict[str, Any]] = []
        for item in items:
            try:
                if hasattr(item, "to_dict"):
                    out.append(item.to_dict())
            except Exception as e:
                logger.error(
                    f"Error serializing observation {getattr(item, 'id', 'unknown')}: {e}"
                )
        return out


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
        code=report_data.get("code") or {},
        subject=report_data.get("subject") or {},
        conclusion=report_data.get("conclusion"),
        effective_datetime=_parse_datetime(report_data.get("effective_datetime")),
    )
    assert_valid_fhir(new_report)

    async with AsyncSessionLocal() as session:
        session.add(new_report)
        await session.commit()
        await session.refresh(new_report)

    return new_report


async def get_diagnostic_report(
    report_id: str | UUID,
    tenant_id: str | UUID | None = None,
) -> Optional[DiagnosticReport]:
    """Get diagnostic report by ID.

    ``tenant_id`` optionally scopes the lookup so cross-tenant reads are
    impossible at the service level. ``None`` preserves unscoped behaviour
    for internal callers that have already verified access.
    """
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(report_id, str):
        try:
            report_id = UUID(report_id)
        except ValueError:
            return None

    predicates = [DiagnosticReport.id == report_id]
    if tenant_id is not None:
        try:
            tid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        except (ValueError, TypeError):
            return None
        predicates.append(DiagnosticReport.tenant_id == tid)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiagnosticReport).where(*predicates)
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

    # ORM-shape input (snake_case) — coerce types directly. The /fhir/* POST
    # endpoints speak ORM-shape; FHIR parsing lives only at the import boundary.
    subject = medication_data.get("subject") or {}

    patient_id = None
    patient_id_str = medication_data.get("patient_id") or _extract_patient_id(subject)
    if patient_id_str:
        try:
            patient_id = UUID(str(patient_id_str))
        except (ValueError, TypeError):
            pass

    if not patient_id:
        logger.error("Cannot create medication: No patient_id found")
        return None

    status_str = (medication_data.get("status") or "ACTIVE").upper()
    new_med = Medication(
        tenant_id=tenant_id,
        patient_id=patient_id,
        code=medication_data.get("code") or {},
        status=status_str,
        subject=subject,
        start_date=_parse_date(medication_data.get("start_date")),
        end_date=_parse_date(medication_data.get("end_date")),
        frequency=medication_data.get("frequency"),
    )
    assert_valid_fhir(new_med)

    async with AsyncSessionLocal() as session:
        session.add(new_med)
        await session.commit()
        await session.refresh(new_med)

    # Automatically create medication reminders if timing is specified
    # (domain logic — reads the raw timing input, not the converted value)
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


async def get_medication(
    medication_id: str | UUID,
    tenant_id: str | UUID | None = None,
) -> Optional[Medication]:
    """Get medication by ID.

    ``tenant_id`` optionally scopes the lookup so cross-tenant reads are
    impossible at the service level. ``None`` preserves unscoped behaviour
    for internal callers that have already verified access.
    """
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(medication_id, str):
        try:
            medication_id = UUID(medication_id)
        except ValueError:
            return None

    predicates = [Medication.id == medication_id]
    if tenant_id is not None:
        try:
            tid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        except (ValueError, TypeError):
            return None
        predicates.append(Medication.tenant_id == tid)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Medication).where(*predicates)
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


async def map_observations_to_biomarkers(
    db, observations, auto_create_missing: bool = True
) -> Dict[str, Any]:
    """Map raw FHIR observations from integrations to BiomarkerDefinitions.

    Creates new definitions if they do not exist, unless auto_create_missing
    is False.

    Returns a dict ``{"mapped": <int>, "dropped_invalid": <int>}`` so callers
    (sync endpoints, background task) can surface partial-success to the
    user instead of silently reporting zero results as "success".
    """
    from app.models.biomarker_model import BiomarkerDefinition
    import re
    import logging

    logger = logging.getLogger(__name__)

    # Write-time FHIR gate: drop resources that cannot be projected to valid
    # FHIR before biomarker mapping/persistence. This single chokepoint covers
    # every integration route (background sync, manual sync, webhook, bridge,
    # and the FHIR-server provider's pull_now path) since they all funnel here.
    observations, dropped = validate_and_filter_observations(observations, logger)
    if dropped:
        logger.info(
            "Dropped %d invalid observation(s) before biomarker mapping", dropped
        )

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

            if not bdef and text and auto_create_missing:
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

    return {"mapped": len(observations), "dropped_invalid": dropped}
