#!/usr/bin/env python3
"""
Seed a FHIR R4 server (e.g. local HAPI) with sample Observations for testing the
`fhir_server` integration.

Creates a Patient, then posts LOINC-coded lab + vital Observations (random
values within realistic reference ranges) spread across the last N months —
exactly the shape `FhirServerProvider.pull_data` consumes. The Biomarker Engine
resolves these by LOINC code on sync.

Usage:
    python scripts/seed_fhir_server.py http://localhost:8095/fhir
    python scripts/seed_fhir_server.py http://localhost:8095/fhir --months 24 --per-type 6
    python scripts/seed_fhir_server.py http://localhost:8095/fhir --patient 123

Requires the server to accept unauthenticated writes (vanilla HAPI does).
"""
import argparse
import asyncio
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import httpx

# (loinc, display, unit, unit_code, category, low, high)
PANEL = [
    ("2345-7", "Glucose", "mg/dL", "mg/dL", "laboratory", 70, 110),
    ("2093-3", "Cholesterol Total", "mg/dL", "mg/dL", "laboratory", 120, 240),
    ("2085-8", "Cholesterol HDL", "mg/dL", "mg/dL", "laboratory", 35, 80),
    ("18262-6", "Cholesterol LDL", "mg/dL", "mg/dL", "laboratory", 60, 160),
    ("2571-8", "Triglyceride", "mg/dL", "mg/dL", "laboratory", 50, 200),
    ("2160-0", "Creatinine", "mg/dL", "mg/dL", "laboratory", 0.6, 1.3),
    ("718-7", "Hemoglobin", "g/dL", "g/dL", "laboratory", 12, 17),
    ("8867-4", "Heart Rate", "/min", "/min", "vital-signs", 55, 105),
    ("8480-6", "Systolic Blood Pressure", "mmHg", "mmHg", "vital-signs", 100, 145),
    ("8462-4", "Diastolic Blood Pressure", "mmHg", "mmHg", "vital-signs", 60, 95),
    ("29463-7", "Body Weight", "kg", "kg", "vital-signs", 65, 90),
    ("8310-5", "Body Temperature", "degC", "degC", "vital-signs", 36.3, 37.4),
    ("59408-5", "Oxygen Saturation", "%", "%", "vital-signs", 94, 100),
]


def _obs(patient_ref, code, display, unit, unit_code, category, low, high, when):
    value = round(random.uniform(low, high), 1 if high - low < 10 else 0)
    interpretation = "N" if low <= value <= high else ("H" if value > high else "L")
    return {
        "resourceType": "Observation",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": category}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}], "text": display},
        "subject": {"reference": patient_ref},
        "effectiveDateTime": when,
        "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit_code},
        "interpretation": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": interpretation}]}],
        "referenceRange": [{"low": {"value": low, "unit": unit}, "high": {"value": high, "unit": unit}}],
    }


async def seed(base_url: str, patient_id: str | None, per_type: int, months: int) -> None:
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as http:
        # 1. Patient
        if patient_id:
            patient_ref = f"Patient/{patient_id}"
            print(f"Using existing patient {patient_ref}")
        else:
            r = await http.post(f"{base}/Patient", json={
                "resourceType": "Patient",
                "name": [{"family": "Synthea", "given": ["Test"]}],
                "gender": "unknown", "birthDate": "1985-01-01",
            })
            r.raise_for_status()
            patient_id = r.json()["id"]
            patient_ref = f"Patient/{patient_id}"
            print(f"Created {patient_ref}")

        # 2. Build a transaction bundle of Observations spread over `months`.
        now = datetime.now(timezone.utc)
        entries = []
        for loinc, display, unit, ucode, cat, low, high in PANEL:
            for i in range(per_type):
                when = (now - timedelta(days=random.randint(1, max(months * 30, 1)))).isoformat()
                entries.append({
                    "request": {"method": "POST", "url": "Observation"},
                    "resource": _obs(patient_ref, loinc, display, unit, ucode, cat, low, high, when),
                })
        random.shuffle(entries)
        bundle = {"resourceType": "Bundle", "type": "transaction", "entry": entries}

        # 3. POST the transaction
        r = await http.post(f"{base}", json=bundle, headers={"Content-Type": "application/fhir+json"})
        r.raise_for_status()
        result = r.json()
        created = sum(1 for e in result.get("entry", []) if e.get("response", {}).get("status", "").startswith("2"))
        print(f"Posted {len(entries)} Observations across {len(PANEL)} LOINC codes; {created} created.")
        print(f"\nNow sync the fhir_server integration (auth_mode=none) to pull these into the Biomarker Engine.")


def main() -> None:
    p = argparse.ArgumentParser(description="Seed a FHIR server with sample Observations.")
    p.add_argument("base_url", help="FHIR base URL, e.g. http://localhost:8095/fhir")
    p.add_argument("--patient", help="Existing Patient id to attach Observations to (created otherwise)")
    p.add_argument("--per-type", type=int, default=4, help="Observations per LOINC code (default 4)")
    p.add_argument("--months", type=int, default=12, help="Spread over this many months (default 12)")
    args = p.parse_args()
    asyncio.run(seed(args.base_url, args.patient, args.per_type, args.months))


if __name__ == "__main__":
    main()
