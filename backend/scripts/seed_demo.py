#!/usr/bin/env python3
"""
Seed a deterministic demo tenant + user + clinical data for UI screenshot capture.

Creates (idempotently):
  - Tenant "Demo Clinic" (slug: demo-clinic)
  - Admin user demo@healthassistant.local / Demo1234!
  - 3 demo patients in that tenant
  - Comprehensive clinical data for the primary patient (Maria Papadopoulou):
    - Biomarkers: Glucose, Cholesterol, Blood Pressure
    - Medications: Metformin, Vitamin D3
    - Allergies: Peanuts
    - Clinical Events: Annual Checkup
    - Examinations: Routine assessment

The captured screenshots in docs/images/ are meant to be reproducible, so this
seed is the single source of truth for what the demo pages should contain.
Re-run safely — existing rows are updated or left untouched.
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.core.security import get_password_hash
from app.models.enums import Gender, Role
from app.models.fhir.patient import Patient, Observation
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.import_service import ImportService

# Default credentials — overridable via the root .env
DEMO_EMAIL = os.getenv("HA_DEMO_EMAIL", "demo@healthassistant.local")
DEMO_PASSWORD = os.getenv("HA_DEMO_PASSWORD", "Demo1234!")
DEMO_TENANT_NAME = "Demo Clinic"
DEMO_TENANT_SLUG = "demo-clinic"

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



    created, updated, errors, warnings, _ = await import_service.restore_fhir_bundle(bundle, tenant_id)
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

async def seed() -> None:
    if not DATABASE_AVAILABLE:
        print("❌ Database is not available. Check DATABASE_URL in backend/.env")
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        # 1. Tenant
        tenant = (
            await session.execute(
                select(TenantModel).where(TenantModel.slug == DEMO_TENANT_SLUG)
            )
        ).scalar_one_or_none()
        if not tenant:
            tenant = TenantModel(
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
                email=DEMO_EMAIL,
                hashed_password=get_password_hash(DEMO_PASSWORD),
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
            from sqlalchemy import text
            obs_exists = (await session.execute(
                select(Observation).where(Observation.subject["reference"].astext == f"Patient/{primary_patient_id}").limit(1)
            )).scalar_one_or_none()
            
            if not obs_exists:
                await seed_clinical_data(session, tenant.id, primary_patient_id, user.id)
                print(f"✅ Seeded comprehensive clinical data for Maria Papadopoulou")
            else:
                print(f"⚠️  Clinical data already exists for primary patient.")

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

if __name__ == "__main__":
    asyncio.run(seed())
