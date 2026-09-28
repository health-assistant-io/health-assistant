#!/usr/bin/env python3
"""Seed a deterministic demo tenant + user + clinical data for UI screenshot capture.

Creates (idempotently):
  - Tenant "Demo Clinic" (slug: demo-clinic)
  - Admin user demo@healthassistant.local / Demo1234!
  - 3 demo patients in that tenant
  - Comprehensive clinical data for the primary patient (Maria Papadopoulou):
    - Biomarkers: Glucose, Cholesterol, Blood Pressure
    - STATE Biomarker: SARS-CoV-2 PCR (Positive/Negative timeline) — exercises
      the StateTimeline + categorical history-table rendering.
    - Medications: Metformin, Vitamin D3
    - Allergies: Peanuts
    - Clinical Events: Annual Checkup
    - Examinations: Routine assessment
  - Mock chat AI (provider_type "mock": deterministic scripted model that
    drives the real graph + DB tools — no API key needed; skip with
    HA_AI_MOCK=0)

The captured screenshots in docs/images/ are meant to be reproducible, so this
seed is the single source of truth for what the demo pages should contain.
Re-run safely — existing rows are updated or left untouched.

§13 guard rails (plan 16 H7) — the seeder refuses anything that is not a
demo target, loudly and non-zero (exit 2), **before any demo data is
written**:

* target guard: the database must be PostgreSQL and literally named
  ``*_demo`` (deployment.md: ``neuro_health_demo``). Anything else —
  including a dev/production database — is refused before it is touched.
* instance guard: ``instance_settings.demo_mode`` must be ``true``.
  ``--init-demo`` may initialize it — but only on an EMPTY demo database
  (no instance facts, no users/tenants/patients); anything else is
  refused. Unreadable facts (unmigrated schema) refuse too — a target
  that cannot prove it is a demo is not a demo (fail-closed, §4.1).

Usage (interpreter with the app's dependencies, e.g. ``venv/bin/python``):

    # The demo stack's database (docker-compose.demo.yml runs this):
    DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/neuro_health_demo \\
        python scripts/seed_demo.py

    # First run against a fresh, migrated, still-empty *_demo database:
    python scripts/seed_demo.py --database-url <url ending in _demo> --init-demo

There is deliberately no ``--reset`` here: demo resets are volume-level —
the demo tree's ``reset-demo.sh`` wipes the DB volume and the stack
re-seeds on boot. The seeder itself is idempotent, so re-running never
duplicates rows.

Exit codes: 0 seeded (or already seeded), 2 refused by a §13 guard rail,
1 unexpected error.
"""

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.instance_state import AUTH_MODE_KEY, DEMO_MODE_KEY  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.models.enums import BiomarkerValueType, CatalogScope, CodingSystem, Gender, Role  # noqa: E402
from app.models.biomarker_model import (  # noqa: E402
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerState,
)
from app.models.fhir.patient import Patient, Observation  # noqa: E402
from app.models.document_model import DocumentModel  # noqa: E402
from app.models.examination_model import ExaminationModel  # noqa: E402
from app.models.ai_provider_model import AIProviderModel, AIModel, AITaskAssignment  # noqa: E402
from app.models.enums import AIScope  # noqa: E402
from app.models.tenant_model import TenantModel  # noqa: E402
from app.models.user_model import UserModel  # noqa: E402
from app.services.import_service import ImportService  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

# Default credentials — overridable via the root .env
DEMO_EMAIL = os.getenv("HA_DEMO_EMAIL", "demo@healthassistant.local")
DEMO_PASSWORD = os.getenv("HA_DEMO_PASSWORD", "Demo1234!")
DEMO_TENANT_NAME = "Demo Clinic"
DEMO_TENANT_SLUG = "demo-clinic"

# Deterministic UUIDs so outstanding JWTs survive the daily reset (which wipes
# the DB volume + re-seeds). With random IDs, every reset mints a new
# tenant_id/user_id, so all existing tokens silently break — /auth/validate
# still passes (stateless JWT) but every data query filters by a tenant_id that
# no longer exists and returns empty results. Fixed IDs mean a reset recreates
# the exact same entities the tokens point at, so demo visitors never see a
# broken/empty app or have to manually log out + back in.
DEMO_TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
DEMO_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
# Patient UUIDs are deterministic too — the frontend stores the selected
# patient ID in localStorage; if a reset mints new UUIDs, the stored ID points
# at a ghost and "No patient selected" appears. Index 0 = Maria (primary).
DEMO_PATIENT_IDS = [
    UUID("33333333-3333-4333-8333-333333333301"),
    UUID("33333333-3333-4333-8333-333333333302"),
    UUID("33333333-3333-4333-8333-333333333303"),
]

# Mock AI provider (deterministic scripted chat model — no API key, no
# network). Seeded by default so the AI chat demo + `ai-chat` screenshot work
# after every reset; set HA_AI_MOCK=0 to skip (e.g. when demoing a real key).
# A tenant/user-scope provider configured later via the UI outranks it.
SEED_MOCK_AI = os.getenv("HA_AI_MOCK", "1") not in ("0", "false", "False")
MOCK_AI_PROVIDER_NAME = "Mock Medical LLM (demo)"


# ---------------------------------------------------------------------------
# §13 guard rails — refuse anything that is not a demo target/instance
# (plan 16 H7; mirrors the family's scripts/seed-demo.py pattern).
# ---------------------------------------------------------------------------


class Refusal(Exception):
    """A §13 guard rail refused the run — print loud, exit non-zero."""


def ensure_demo_target(url: str) -> str:
    """Target guard (§13): a PostgreSQL database literally named ``*_demo``.

    Health is a Class S server product — there is no SQLite/demo-dir
    flavor to allow, so any non-PostgreSQL backend and any non-``*_demo``
    name is refused **before the database is touched**.
    """
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    if not backend.startswith("postgresql"):
        raise Refusal(
            f"unsupported database backend {backend!r} — health demo data may "
            "only be seeded into a PostgreSQL *_demo database "
            "(identity-auth §13)."
        )
    name = parsed.database or ""
    if not name.endswith("_demo"):
        raise Refusal(
            f"target database {name!r} is not a demo database — expected a "
            "name ending '_demo' (deployment.md: neuro_health_demo); "
            "refusing to seed (identity-auth §13)."
        )
    return url


async def ensure_demo_instance(session: AsyncSession, *, init_demo: bool) -> str:
    """Instance guard (§13): ``instance_settings.demo_mode`` must be ``true``.

    ``--init-demo`` may write it — but only on an **empty** demo database;
    anything else is refused (never re-flag an existing instance as demo).
    Unreadable facts (unmigrated schema) refuse too: a target that cannot
    prove it is a demo is not a demo (fail-closed, §4.1).
    """
    from app.models.instance_setting_model import InstanceSettingModel

    try:
        row = await session.get(InstanceSettingModel, DEMO_MODE_KEY)
    except Exception as e:  # unreadable ⇒ unprovable ⇒ refuse
        raise Refusal(
            f"cannot read instance_settings on the target ({type(e).__name__}: "
            f"{e}) — a demo target must be a migrated *_demo database; "
            "refusing (identity-auth §13)."
        ) from e
    if row is not None and str(row.value).strip().lower() == "true":
        return "demo_mode=true (instance_settings)"
    if not init_demo:
        raise Refusal(
            "instance_settings.demo_mode is not 'true' — refusing to seed a "
            "non-demo instance (identity-auth §13). Use --init-demo to "
            "initialize an EMPTY demo database."
        )
    if not await _instance_is_empty(session):
        raise Refusal(
            "--init-demo requires an EMPTY demo database (found existing "
            "instance data) — refusing to re-flag an existing instance as "
            "demo (identity-auth §13)."
        )
    session.add(InstanceSettingModel(key=DEMO_MODE_KEY, value="true"))
    await session.commit()
    return "demo_mode=true (--init-demo)"


async def _instance_is_empty(session: AsyncSession) -> bool:
    """True only when the demo database holds no instance data at all."""
    from app.models.instance_setting_model import InstanceSettingModel

    for key in (DEMO_MODE_KEY, AUTH_MODE_KEY):
        try:
            if await session.get(InstanceSettingModel, key) is not None:
                return False
        except Exception as e:
            raise Refusal(
                f"cannot read instance_settings on the target "
                f"({type(e).__name__}: {e}) — refusing (identity-auth §13)."
            ) from e
    for model in (UserModel, TenantModel, Patient):
        try:
            if (
                await session.execute(select(model.id).limit(1))
            ).first() is not None:
                return False
        except Exception as e:
            raise Refusal(
                f"cannot inspect {model.__tablename__!r} on the target "
                f"({type(e).__name__}: {e}) — the demo target must be fully "
                "migrated; refusing (identity-auth §13)."
            ) from e
    return True


RICH_OCR_TEXT = """PATIENT & LABORATORY INFORMATION
Patient Name: Maria Papadopoulou
Date of Birth: 03/14/1986
Date Collected: 03/18/2026
Fasting Status: Yes (12 Hours)

1. Complete Blood Count (CBC) with Differential
Test Name Result Flag Units Reference Range
White Blood Cell (WBC) 6.8 Normal x10^3/µL 3.8 - 10.8
Red Blood Cell (RBC) 4.90 Normal x10^6/µL 4.20 - 5.80
Hemoglobin (Hb) 14.5 Normal g/dL 13.2 - 17.1
Hematocrit (Hct) 43.5 Normal % 38.5 - 50.0
Mean Corpuscular Vol (MCV) 88.0 Normal fL 80.0 - 100.0
Mean Corpuscular Hgb (MCH) 29.5 Normal pg 27.0 - 33.0
MCHC 33.5 Normal g/dL 32.0 - 36.0
Platelet Count 245 Normal x10^3/µL 140 - 400
Neutrophils (Absolute) 4.1 Normal x10^3/µL 1.5 - 7.8
Lymphocytes (Absolute) 1.9 Normal x10^3/µL 0.8 - 3.3
Monocytes (Absolute) 0.5 Normal x10^3/µL 0.2 - 0.9
Eosinophils (Absolute) 0.2 Normal x10^3/µL 0.0 - 0.5
Basophils (Absolute) 0.05 Normal x10^3/µL 0.0 - 0.2

2. Comprehensive Metabolic Panel (CMP)
Test Name Result Flag Units Reference Range
Glucose (Fasting) 112 HIGH mg/dL 65 - 99
BUN (Blood Urea Nitrogen) 14 Normal mg/dL 7 - 25
Creatinine 0.85 Normal mg/dL 0.60 - 1.30
eGFR >90 Normal mL/min/1.73 >59
BUN/Creatinine Ratio 16.5 Normal n/a 9.0 - 20.0
Sodium 140 Normal mmol/L 135 - 146
Potassium 4.2 Normal mmol/L 3.5 - 5.3
Chloride 102 Normal mmol/L 98 - 110
Carbon Dioxide (CO2) 26 Normal mmol/L 20 - 32
Calcium 9.4 Normal mg/dL 8.6 - 10.3
Total Protein 7.2 Normal g/dL 6.1 - 8.1
Albumin 4.5 Normal g/dL 3.6 - 5.1
Globulin 2.7 Normal g/dL 1.9 - 3.7
Bilirubin, Total 0.6 Normal mg/dL 0.2 - 1.2
Alkaline Phosphatase (ALP) 65 Normal U/L 36 - 130
AST (SGOT) 22 Normal U/L 10 - 40
ALT (SGPT) 28 Normal U/L 9 - 46

3. Lipid Panel
Test Name Result Flag Units Reference Range
Cholesterol, Total 225 HIGH mg/dL < 200
Triglycerides 140 Normal mg/dL < 150
HDL Cholesterol 48 Normal mg/dL > 40
LDL Cholesterol (Calc) 149 HIGH mg/dL < 100
Cholesterol/HDL Ratio 4.7 Normal n/a < 5.0
VLDL Cholesterol (Calc) 28 Normal mg/dL < 30

4. Thyroid & Iron Studies
Test Name Result Flag Units Reference Range
TSH 2.45 Normal mIU/L 0.40 - 4.50
Free T4 1.2 Normal ng/dL 0.8 - 1.8
Iron, Total 85 Normal µg/dL 50 - 170
Total Iron Binding (TIBC) 320 Normal µg/dL 250 - 450
Transferrin Saturation 26.5 Normal % 15.0 - 50.0
Ferritin 65 Normal ng/mL 30 - 400

5. Vitamins & Inflammatory Markers
Test Name Result Flag Units Reference Range
Vitamin D, 25-Hydroxy 18.5 LOW ng/mL 30.0 - 100.0
Vitamin B12 450 Normal pg/mL 200 - 1100
C-Reactive Protein (hs-CRP) 1.2 Normal mg/L < 3.0
Hemoglobin A1c (HbA1c) 5.8 HIGH % < 5.7

Clinical Laboratory Notes / Interpretation Summary:
- Glucose & HbA1c: Fasting glucose is mildly elevated at 112 mg/dL, and HbA1c is at 5.8%. These values fall into the "Prediabetes" range. Dietary modifications and exercise are usually recommended.
- Lipid Panel: Total Cholesterol (225) and LDL (149) are elevated indicating borderline-high risk for hyperlipidemia.
- Vitamin D: Value is 18.5 ng/mL, indicating a Vitamin D deficiency (optimal is >30 ng/mL). Supplementation is commonly advised by physicians for this level.
- All other markers (CBC, liver enzymes, kidney function, and thyroid) are within normal, healthy limits.
"""

DEMO_PATIENTS = [
    {
        "name": {"given": ["Maria"], "family": "Papadopoulou"},
        "gender": Gender.FEMALE,
        "birth_date": date(1986, 3, 14),
        "mrn": "DEMO-0001",
        "telecom": [{"system": "phone", "value": "+30 210 555 0101"}],
        "address": [{"city": "Athens", "country": "Greece"}],
    },
    {
        "name": {"given": ["Nikos"], "family": "Georgiou"},
        "gender": Gender.MALE,
        "birth_date": date(1979, 11, 2),
        "mrn": "DEMO-0002",
        "telecom": [{"system": "phone", "value": "+30 210 555 0142"}],
        "address": [{"city": "Thessaloniki", "country": "Greece"}],
    },
    {
        "name": {"given": ["Eleni"], "family": "Kontou"},
        "gender": Gender.FEMALE,
        "birth_date": date(1992, 7, 21),
        "mrn": "DEMO-0003",
        "telecom": [{"system": "email", "value": "eleni.kontou@example.com"}],
        "address": [{"city": "Patras", "country": "Greece"}],
    },
]

async def seed_clinical_data(session, tenant_id: UUID, patient_id: UUID, user_id: UUID) -> None:
    """Seed comprehensive clinical data using ImportService."""
    import_service = ImportService(session)
    
    # FHIR Bundle for clinical data (deterministic dates for stable screenshots)
    # Using 2026-06-15T10:00:00Z as "today" (matching FIXED_NOW in capture.mjs)
    base_date = "2026-06-15T10:00:00Z"
    
    # Helper to generate multiple values for a biomarker
    def create_observation(date_str: str, loinc: str, text: str, value: float, unit: str, ranges=None):
        obs = {
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "code": {
                    "text": text,
                    "coding": [{"system": "http://loinc.org", "code": loinc}]
                },
                "subject": {"reference": f"Patient/{patient_id}"},
                "effectiveDateTime": date_str,
                "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}
            }
        }
        if ranges:
            obs["resource"]["referenceRange"] = ranges
        return obs

    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": []
    }

    # Generate 10 days of data ending at base_date
    base = datetime.fromisoformat(base_date.replace("Z", "+00:00"))
    for i in range(10):
        # 1 day intervals, varying the time slightly
        dt = base - timedelta(days=(9-i))
        date_str = dt.isoformat()
        if "+00:00" in date_str:
            date_str = date_str.replace("+00:00", "Z")
        elif not date_str.endswith("Z"):
            date_str += "Z"
        
        # 1. Glucose (LOINC 2339-0) - 80 to 110
        val_gluc = 85 + (i * 3) % 25
        bundle["entry"].append(create_observation(date_str, "2339-0", "Glucose", val_gluc, "mg/dL", [{"low": {"value": 70}, "high": {"value": 99}}]))
        
        # 2. Total Cholesterol (LOINC 2093-3) - 170 to 195
        val_chol = 180 + (i * 2) % 15 - (i % 3)
        bundle["entry"].append(create_observation(date_str, "2093-3", "Total Cholesterol", val_chol, "mg/dL", [{"low": {"value": 120}, "high": {"value": 200}}]))
        
        # 3. Heart Rate (LOINC 8867-4) - 65 to 85
        val_hr = 70 + (i * 4) % 15 - (i % 2)
        bundle["entry"].append(create_observation(date_str, "8867-4", "Heart rate", val_hr, "/min", [{"low": {"value": 60}, "high": {"value": 100}}]))
        
        # 4. Body Temperature (LOINC 8310-5) - 36.5 to 37.2
        val_temp = 36.6 + ((i * 0.1) % 0.6)
        bundle["entry"].append(create_observation(date_str, "8310-5", "Body temperature", round(val_temp, 1), "Cel", [{"low": {"value": 36.1}, "high": {"value": 37.2}}]))

        # 5. Systolic Blood Pressure (LOINC 8480-6) - 110 to 125
        val_sys = 115 + (i * 2) % 10
        bundle["entry"].append(create_observation(date_str, "8480-6", "Systolic blood pressure", val_sys, "mm[Hg]", [{"low": {"value": 90}, "high": {"value": 120}}]))

        # 6. Diastolic Blood Pressure (LOINC 8462-4) - 70 to 80
        val_dia = 75 + (i * 1) % 5
        bundle["entry"].append(create_observation(date_str, "8462-4", "Diastolic blood pressure", val_dia, "mm[Hg]", [{"low": {"value": 60}, "high": {"value": 80}}]))

    bundle["entry"].extend([
        # 2. Medications
        {
            "resource": {
                "resourceType": "MedicationStatement",
                "status": "active",
                "medicationCodeableConcept": {"text": "Metformin 500mg"},
                "subject": {"reference": f"Patient/{patient_id}"},
                "effectivePeriod": {"start": "2025-10-15"},
                "dosage": [{"text": "1 tablet twice daily", "timing": {"repeat": {"frequency": 2, "period": 1, "periodUnit": "d"}}}],
                "reasonCode": [{"text": "Type 2 Diabetes prevention"}]
            }
        },
            {
                "resource": {
                    "resourceType": "MedicationStatement",
                    "status": "active",
                    "medicationCodeableConcept": {"text": "Vitamin D3 2000IU"},
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "effectivePeriod": {"start": "2026-01-20"},
                    "dosage": [{"text": "1 capsule daily", "timing": {"repeat": {"frequency": 1, "period": 1, "periodUnit": "d"}}}],
                    "note": [{"text": "Take with fatty meal for better absorption"}]
                }
            },
            # 3. Allergies
            {
                "resource": {
                    "resourceType": "AllergyIntolerance",
                    "clinicalStatus": {
                        "coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]
                    },
                    "verificationStatus": {
                        "coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "code": "confirmed"}]
                    },
                    "category": ["food"],
                    "criticality": "high",
                    "code": {"text": "Peanuts"},
                    "patient": {"reference": f"Patient/{patient_id}"},
                    "note": [{"text": "Severe anaphylactic reaction reported in childhood."}],
                    "reaction": [{"manifestation": [{"text": "Anaphylaxis"}], "severity": "severe"}]
                }
            }
    ])
    

    # 6. Create mock documents
    doc_exists = (await session.execute(
        select(DocumentModel).where(DocumentModel.patient_id == patient_id).limit(1)
    )).scalar_one_or_none()
    
    if not doc_exists:
        from app.core.config import settings
        from pathlib import Path
        
        tenant_dir = Path(settings.UPLOAD_DIR) / str(tenant_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)
        
        project_root = Path(backend_dir).parent
        sample_pdf = project_root / "backend" / "data" / "seeds" / "sample_blood_panel.pdf"
        
        if sample_pdf.exists():
            pdf_content = sample_pdf.read_bytes()
        else:
            pdf_content = b"%PDF-1.4\n1 0 obj <</Type/Catalog/Pages 2 0 R>> endobj\n2 0 obj <</Type/Pages/Count 1/Kids[3 0 R]>> endobj\n3 0 obj <</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>/Contents 4 0 R>> endobj\n4 0 obj <</Length 47>> stream\nBT /F1 24 Tf 100 700 Td (Mock PDF Document) Tj ET\nendstream endobj\nxref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n0000000052 00000 n\n0000000101 00000 n\n0000000188 00000 n\ntrailer <</Size 5/Root 1 0 R>>\nstartxref\n284\n%%EOF\n"

        
        files = [
            "Comprehensive_Blood_Panel_2026.pdf",
            "Annual_Checkup_Notes_2025.pdf",
            "Allergy_Test_Results_2025.pdf",
            "Vaccination_Record.pdf"
        ]
        
        file_paths = []
        for file in files:
            path = tenant_dir / file
            path.write_bytes(pdf_content)
            file_paths.append(str(path))
            
        docs = [
            DocumentModel(
                patient_id=patient_id,
                owner_id=user_id,
                tenant_id=tenant_id,
                filename="Comprehensive_Blood_Panel_2026.pdf",
                file_path=file_paths[0],
                status="completed",
                extracted_text=RICH_OCR_TEXT,
                entities={"biomarkers": ["WBC", "RBC", "Hemoglobin", "Hematocrit", "MCV", "MCH", "Glucose", "BUN", "Creatinine", "Cholesterol, Total", "Vitamin D", "TSH"]}
            ),
            DocumentModel(
                patient_id=patient_id,
                owner_id=user_id,
                tenant_id=tenant_id,
                filename="Annual_Checkup_Notes_2025.pdf",
                file_path=file_paths[1],
                status="completed",
                extracted_text="Routine checkup notes for Maria Papadopoulou.\nWeight is stable. No new complaints. Blood pressure is 115/75.",
                entities={"diagnoses": ["Healthy patient"]}
            ),
            DocumentModel(
                patient_id=patient_id,
                owner_id=user_id,
                tenant_id=tenant_id,
                filename="Allergy_Test_Results_2025.pdf",
                file_path=file_paths[2],
                status="completed",
                extracted_text="Patient: Maria Papadopoulou\nIgE test results indicating strong reaction to peanuts.",
                entities={"allergies": ["Peanuts"]}
            ),
            DocumentModel(
                patient_id=patient_id,
                owner_id=user_id,
                tenant_id=tenant_id,
                filename="Vaccination_Record.pdf",
                file_path=file_paths[3],
                status="completed",
                extracted_text="Vaccination record.\nCOVID-19 Booster: 10/2025\nFlu Shot: 09/2025",
                entities={"medications": ["COVID-19 Vaccine", "Influenza Vaccine"]}
            )
        ]
        session.add_all(docs)
        await session.flush()



    _brr = await import_service.restore_fhir_bundle(bundle, tenant_id)
    _created, _updated, errors, warnings = _brr.created, _brr.updated, _brr.errors, _brr.warnings
    if errors:
        print(f"❌ FHIR Import Errors: {errors}")
        # Not raising immediately so we can see all errors
    if warnings:
        print(f"⚠️ FHIR Import Warnings: {warnings}")
    
    # 5. Examinations (Sidecar format)
    # Check if an examination already exists to prevent duplicate exams
    exam_exists = (await session.execute(
        select(ExaminationModel).where(ExaminationModel.patient_id == patient_id).limit(1)
    )).scalar_one_or_none()
    
    if not exam_exists:
        examinations = [
            {
                "patient_id": str(patient_id),
                "examination_date": "2026-06-10",
                "notes": "Maria presented for a routine checkup. Overall health is excellent. "
                         "Blood glucose levels are stable. Suggested continuation of current supplement regimen.",
                "extraction_status": "completed",
                "diagnoses": ["Healthy patient"]
            },
            {
                "patient_id": str(patient_id),
                "examination_date": "2026-03-15",
                "notes": "Follow-up visit for previous complaints of fatigue. "
                         "Patient reports feeling much better after starting Vitamin D supplementation.",
                "extraction_status": "completed",
                "diagnoses": ["Vitamin D deficiency", "Fatigue (resolved)"]
            },
            {
                "patient_id": str(patient_id),
                "examination_date": "2025-11-20",
                "notes": "Annual physical examination. Patient is actively managing diet and exercise. "
                         "Weight is stable. No new complaints.",
                "extraction_status": "completed",
                "diagnoses": ["Routine physical examination"]
            },
            {
                "patient_id": str(patient_id),
                "examination_date": "2025-08-05",
                "notes": "Patient reported mild allergic reaction (hives) after consuming unknown food at a restaurant. "
                         "Prescribed antihistamines and advised allergy testing.",
                "extraction_status": "completed",
                "diagnoses": ["Allergic reaction", "Urticaria"]
            },
            {
                "patient_id": str(patient_id),
                "examination_date": "2025-02-12",
                "notes": "Consultation for upper respiratory tract infection. "
                         "Symptoms include cough, mild fever, and congestion. Prescribed rest and fluids.",
                "extraction_status": "completed",
                "diagnoses": ["Upper respiratory tract infection"]
            }
        ]
        await import_service.restore_sidecar("examinations.json", examinations, tenant_id, {})

        # Link observations from 2026-06-10 to the 2026-06-10 examination
        from sqlalchemy import update
        
        exam_id_result = await session.execute(
            select(ExaminationModel.id).where(
                ExaminationModel.patient_id == patient_id, 
                ExaminationModel.examination_date == date(2026, 6, 10)
            )
        )
        exam_id = exam_id_result.scalar_one_or_none()
        
        if exam_id:
            target_dt = datetime(2026, 6, 10, 10, 0, tzinfo=timezone.utc)
            await session.execute(
                update(Observation)
                .where(
                    Observation.subject["reference"].astext == f"Patient/{patient_id}",
                    Observation.effective_datetime == target_dt
                )
                .values(examination_id=str(exam_id))
            )


# HL7 v3-ObservationInterpretation system URL — the canonical code system for
# categorical results (POS/NEG/Indeterminate/...). Pre-seeded by
# ``seed_service.seed_biomarker_states`` at startup.
V3_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


async def seed_mock_ai(session, tenant_id: UUID) -> None:
    """Seed the mock chat AI: a ``mock``-type provider + model + a TENANT-scope
    ``chat`` task assignment, idempotently.

    The mock (``app.ai.chat_models.MockMedicalChatModel``) is a scripted
    LangChain model that drives the real agentic graph with real DB tools and
    answers from their results — so the AI chat demo and the ``ai-chat``
    screenshot work deterministically without any API key. Skip with
    ``HA_AI_MOCK=0``. A provider configured later via the UI at TENANT/USER
    scope (or a SYSTEM assignment with higher priority) outranks this one.
    """
    provider = (
        await session.execute(
            select(AIProviderModel).where(
                AIProviderModel.name == MOCK_AI_PROVIDER_NAME,
                AIProviderModel.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not provider:
        provider = AIProviderModel(
            name=MOCK_AI_PROVIDER_NAME,
            scope=AIScope.TENANT,
            provider_type="mock",
            api_base="mock://local",  # NOT NULL column; never dialed
            api_key=None,
            is_local=True,
            is_active=True,
            tenant_id=tenant_id,
            settings={},
        )
        session.add(provider)
        await session.flush()
        print("✅ Seeded mock AI provider (deterministic, no API key)")
    else:
        print("⚠️  Mock AI provider already exists")

    model = (
        await session.execute(
            select(AIModel).where(
                AIModel.provider_id == provider.id,
                AIModel.model_name == "mock-medical",
            )
        )
    ).scalar_one_or_none()
    if not model:
        model = AIModel(
            provider_id=provider.id,
            name="Mock Medical (scripted)",
            model_name="mock-medical",
            description="Deterministic scripted chat model for demos/screenshots.",
            is_active=True,
            temperature=0.0,
        )
        session.add(model)
        await session.flush()
        print("✅ Seeded mock AI model: mock-medical")

    assignment = (
        await session.execute(
            select(AITaskAssignment).where(
                AITaskAssignment.task_type == "chat",
                AITaskAssignment.scope == AIScope.TENANT,
                AITaskAssignment.tenant_id == tenant_id,
                AITaskAssignment.provider_id == provider.id,
            )
        )
    ).scalar_one_or_none()
    if not assignment:
        session.add(
            AITaskAssignment(
                task_type="chat",
                scope=AIScope.TENANT,
                provider_id=provider.id,
                model_id=model.id,
                is_active=True,
                priority=0,
                tenant_id=tenant_id,
            )
        )
        print("✅ Assigned mock AI to the 'chat' task (TENANT scope)")
    else:
        print("⚠️  Mock AI chat assignment already exists")


async def seed_state_biomarkers(session, tenant_id: UUID, patient_id: UUID, user_id: UUID) -> None:
    """Seed a STATE biomarker (SARS-CoV-2 PCR) + an alternating POS/NEG
    observation timeline.

    This exercises the StateTimeline UI + the categorical history-table
    rendering. STATE biomarkers carry their value in
    ``valueCodeableConcept`` (numeric columns are NULL by design), so they
    can't go through the legacy FHIR-bundle path above — the validator
    requires a BiomarkerDefinition with ``value_type=STATE`` + matching
    ``allowed_states`` to exist first.

    Idempotent: re-runs skip rows that already exist (matched by biomarker
    slug + observation effective_datetime).
    """
    # 1. Resolve the canonical POS / NEG BiomarkerState rows (pre-seeded by
    #    seed_service.seed_biomarker_states). Skip silently if the state
    #    catalog isn't loaded — never crash the demo seed over a missing
    #    optional catalog row.
    pos_state = (
        await session.execute(
            select(BiomarkerState).where(
                BiomarkerState.code == "POS",
                BiomarkerState.system == V3_SYSTEM,
            )
        )
    ).scalar_one_or_none()
    neg_state = (
        await session.execute(
            select(BiomarkerState).where(
                BiomarkerState.code == "NEG",
                BiomarkerState.system == V3_SYSTEM,
            )
        )
    ).scalar_one_or_none()
    if not pos_state or not neg_state:
        print(
            "⚠️  POS/NEG BiomarkerState rows not found — run the biomarker_states "
            "seed first. Skipping state-biomarker demo data."
        )
        return

    # 2. Create (or reuse) the SARS-CoV-2 PCR definition. LOINC 94500-6 is
    #    "SARS-CoV-2 (COVID-19) RNA [Presence] in Respiratory specimen by NAA
    #    with probe detection" — the canonical PCR test.
    bio_slug = "sars-cov-2-pcr"
    bio = (
        await session.execute(
            select(BiomarkerDefinition).where(BiomarkerDefinition.slug == bio_slug)
        )
    ).scalar_one_or_none()
    if not bio:
        bio = BiomarkerDefinition(
            slug=bio_slug,
            coding_system=CodingSystem.LOINC,
            code="94500-6",
            name="SARS-CoV-2 PCR",
            aliases=["COVID-19 PCR", "Coronavirus PCR"],
            info="Detects SARS-CoV-2 RNA in respiratory specimens via nucleic acid amplification. Positive indicates active infection.",
            value_type=BiomarkerValueType.STATE,
            supports_multi_state=False,
            scope=CatalogScope.SYSTEM,
        )
        session.add(bio)
        await session.flush()
        print(f"✅ Created STATE biomarker: {bio.name}")
    else:
        # Backfill value_type on legacy re-seeds where the row was created
        # before state-biomarkers shipped.
        if bio.value_type != BiomarkerValueType.STATE:
            bio.value_type = BiomarkerValueType.STATE

    # 3. Attach allowed_states (POS=abnormal, NEG=normal) idempotently.
    #    Query the join table directly — accessing bio.allowed_states would
    #    lazy-load inside an async context and raise MissingGreenlet.
    existing_allowed = {
        a.state_id
        for a in (
            await session.execute(
                select(BiomarkerAllowedState).where(
                    BiomarkerAllowedState.biomarker_id == bio.id
                )
            )
        ).scalars().all()
    }
    if pos_state.id not in existing_allowed:
        session.add(
            BiomarkerAllowedState(
                biomarker_id=bio.id,
                state_id=pos_state.id,
                is_normal=False,
                sort_order=0,
            )
        )
    if neg_state.id not in existing_allowed:
        session.add(
            BiomarkerAllowedState(
                biomarker_id=bio.id,
                state_id=neg_state.id,
                is_normal=True,
                sort_order=1,
            )
        )
    await session.flush()

    # 4. Seed a 6-point timeline telling a realistic clinical story:
    #    baseline NEG → acute infection POS → still POS → recovered NEG →
    #    routine NEG → reinfection POS (latest, for screenshot timeliness).
    timeline = [
        ("2026-01-10T09:00:00Z", neg_state, "Negative"),
        ("2026-02-15T14:30:00Z", pos_state, "Positive"),
        ("2026-02-25T10:00:00Z", pos_state, "Positive"),
        ("2026-03-08T08:15:00Z", neg_state, "Negative"),
        ("2026-05-12T11:00:00Z", neg_state, "Negative"),
        ("2026-06-15T10:00:00Z", pos_state, "Positive"),
    ]
    created_obs = 0
    for iso_ts, state, display in timeline:
        effective_dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        # Idempotent: skip if any observation for this patient + biomarker +
        # timestamp already exists (re-runs shouldn't duplicate).
        exists = (
            await session.execute(
                select(Observation.id).where(
                    Observation.patient_id == patient_id,
                    Observation.biomarker_id == bio.id,
                    Observation.effective_datetime == effective_dt,
                )
            )
        ).scalar_one_or_none()
        if exists:
            continue
        session.add(
            Observation(
                tenant_id=tenant_id,
                status="final",
                code={
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": "94500-6",
                            "display": "SARS-CoV-2 PCR",
                        }
                    ],
                    "text": "SARS-CoV-2 PCR",
                },
                subject={"reference": f"Patient/{patient_id}"},
                patient_id=patient_id,
                biomarker_id=bio.id,
                value_codeable_concept={
                    "coding": [
                        {
                            "code": state.code,
                            "system": state.system,
                            "display": display,
                        }
                    ]
                },
                effective_datetime=effective_dt,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        created_obs += 1
    if created_obs:
        await session.flush()
        print(f"✅ Seeded {created_obs} STATE observations for SARS-CoV-2 PCR")


async def seed(
    *,
    database_url: str | None = None,
    init_demo: bool = False,
    session_factory=None,
) -> None:
    """Seed the demo dataset — after the §13 guard rails have their say.

    ``database_url`` defaults to the configured instance URL (the backend
    boot path calls this with no arguments); ``session_factory`` lets the
    CLI bind a dedicated engine for an explicit ``--database-url`` target.
    Raises :class:`Refusal` (exit 2 from the CLI; abort/warn via the boot
    path's ``_abort_or_warn``) when the target is not a provable demo.
    """
    url = database_url or settings.DATABASE_URL
    ensure_demo_target(url)

    factory = session_factory or AsyncSessionLocal
    if factory is None:
        print("❌ Database is not available. Check DATABASE_URL in backend/.env")
        sys.exit(EXIT_ERROR)

    async with factory() as session:
        # §13 instance guard — demo data only ever lands on a demo instance.
        demo_reason = await ensure_demo_instance(session, init_demo=init_demo)
        print(f"✅ Demo target verified: {demo_reason}")

        # 1. Tenant
        tenant = (
            await session.execute(
                select(TenantModel).where(TenantModel.slug == DEMO_TENANT_SLUG)
            )
        ).scalar_one_or_none()
        if not tenant:
            tenant = TenantModel(
                id=DEMO_TENANT_ID,
                name=DEMO_TENANT_NAME,
                slug=DEMO_TENANT_SLUG,
                description="Deterministic demo tenant for UI screenshot capture.",
                is_active=True,
                settings={},
            )
            session.add(tenant)
            await session.flush()
            print(f"✅ Created tenant: {tenant.name} ({tenant.slug})")
        else:
            print(f"⚠️  Tenant already exists: {tenant.name}")

        # 2. User
        user = (
            await session.execute(
                select(UserModel).where(UserModel.email == DEMO_EMAIL)
            )
        ).scalar_one_or_none()
        if not user:
            user = UserModel(
                id=DEMO_USER_ID,
                email=DEMO_EMAIL,
                password_hash=get_password_hash(DEMO_PASSWORD),
                role=Role.ADMIN,
                tenant_id=tenant.id,
                is_active=True,
                settings={},
            )
            session.add(user)
            await session.flush()
            # Link tenant owner for a complete demo tenant.
            tenant.owner_id = user.id
            print(f"✅ Created demo user: {user.email} (ADMIN)")
        else:
            print(f"⚠️  User already exists: {user.email}")

        # 3. Patients
        created_patients = 0
        primary_patient_id = None
        for i, p in enumerate(DEMO_PATIENTS):
            existing = (
                await session.execute(
                    select(Patient).where(Patient.mrn == p["mrn"])
                )
            ).scalar_one_or_none()
            
            if existing:
                if i == 0:
                    primary_patient_id = existing.id
                continue
                
            new_patient = Patient(
                id=DEMO_PATIENT_IDS[i],
                tenant_id=tenant.id,
                created_by=user.id,
                updated_by=user.id,
                **p,
            )
            session.add(new_patient)
            await session.flush()
            if i == 0:
                primary_patient_id = new_patient.id
            created_patients += 1

        # 4. Clinical Data for the primary patient
        if primary_patient_id:
            # Check if clinical data exists (checking Observations)
            obs_exists = (await session.execute(
                select(Observation).where(Observation.subject["reference"].astext == f"Patient/{primary_patient_id}").limit(1)
            )).scalar_one_or_none()
            
            if not obs_exists:
                await seed_clinical_data(session, tenant.id, primary_patient_id, user.id)
                print("✅ Seeded comprehensive clinical data for Maria Papadopoulou")
            else:
                print("⚠️  Clinical data already exists for primary patient.")

            # STATE biomarker (SARS-CoV-2 PCR) + alternating POS/NEG timeline.
            # Idempotent on its own — runs on every seed so re-seeds pick up
            # the state timeline even if the quantity clinical data was
            # already present.
            await seed_state_biomarkers(session, tenant.id, primary_patient_id, user.id)

        # 5. Mock chat AI (deterministic, no API key) unless HA_AI_MOCK=0.
        if SEED_MOCK_AI:
            await seed_mock_ai(session, tenant.id)
        else:
            print("⏭️  Skipping mock AI seed (HA_AI_MOCK=0)")

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            print("⚠️  Integrity error on commit; rolled back.")

        print(f"✅ Demo seed complete: {created_patients} patients created.")

    print()
    print("── Demo credentials ──────────────────────────────")
    print(f"  Email:    {DEMO_EMAIL}")
    print(f"  Password: {DEMO_PASSWORD}")
    print(f"  Role:     ADMIN  (sees all patients in '{DEMO_TENANT_NAME}')")
    print("──────────────────────────────────────────────────")
    print()

# ---------------------------------------------------------------------------
# CLI — argparse + §13 refusal matrix (exit 2 on a guard rail).
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed_demo.py",
        description=(
            "Seed the deterministic Health Assistant demo dataset "
            "(identity-auth §13 — refuses any non-demo target)."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "Target database URL (else DATABASE_URL / POSTGRES_* env). "
            "Must be PostgreSQL named *_demo (deployment.md: "
            "neuro_health_demo)."
        ),
    )
    parser.add_argument(
        "--init-demo",
        action="store_true",
        help=(
            "On an EMPTY *_demo database only: write "
            "instance_settings.demo_mode=true before seeding. Refused on a "
            "non-empty database."
        ),
    )
    parser.epilog = (
        "No --reset flag by design: demo resets are volume-level — the "
        "demo tree's reset-demo.sh wipes the DB volume and the stack "
        "re-seeds on boot. This seeder is idempotent."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url = args.database_url or settings.DATABASE_URL

    factory = None
    try:
        if args.database_url and args.database_url != settings.DATABASE_URL:
            # §13 target guard first: a non-PostgreSQL/non-*_demo URL must
            # REFUSE (exit 2), not crash on async-engine construction below.
            ensure_demo_target(url)
            # Explicit target: bind a dedicated engine so the guards and the
            # seeding hit exactly the URL the operator named.
            engine = create_async_engine(args.database_url)
            factory = async_sessionmaker(
                bind=engine, class_=AsyncSession, expire_on_commit=False
            )

        asyncio.run(
            seed(database_url=url, init_demo=args.init_demo, session_factory=factory)
        )
    except Refusal as refusal:
        print(f"⛔ REFUSED: {refusal}", file=sys.stderr)
        print(
            "   Demo data may only be seeded into a *_demo PostgreSQL "
            "database with instance_settings.demo_mode=true "
            "(identity-auth §13).",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ Demo seed failed: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
