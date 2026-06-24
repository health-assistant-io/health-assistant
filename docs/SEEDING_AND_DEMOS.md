# Seeding and Demos

Creating robust demo data is critical for E2E tests, documentation screenshots, and local development. This project follows specific best practices to ensure seeding is idempotent, deterministic, and maintainable.

## 1. Avoid Raw SQL or `.zip` Imports for Core Seeds

While the system supports ZIP and JSON imports for end-users, relying on external `.zip` files for core developer seeding introduces complexity:
- Binary files are hard to review in Git.
- Schema changes break `.zip` payloads silently until runtime.
- Complex ID mapping is required.

**Best Practice:** Use the `ImportService` programmatically with an embedded FHIR Bundle in a Python script (like `backend/scripts/seed_demo.py`). This gives you:
- Complete control over deterministic dates.
- Type safety and stack traces if schema changes occur.
- Immediate feedback during development.

## 2. Deterministic Execution

Seeds must be **idempotent**. Running `python3 scripts/seed_demo.py` multiple times must not create duplicate tenants, users, or patients.
- Use `scalar_one_or_none()` to check for existing records (e.g., matching a fixed `slug` or `email`).
- Check for existing data (e.g., `Patient.mrn` or `Observation.subject`) before bulk-inserting.

## 3. Date Freezing for Visual Regression

When generating data for screenshots (e.g., `capture_ui.sh`), dates must be relative to a "frozen" present.
In this project, the UI capture scripts (`frontend/tests-e2e/ui-capture/capture.mjs`) freeze the browser clock (e.g., to `2026-06-15`).
Your seed script should generate FHIR resources with absolute dates relative to that same frozen point to ensure charts and relative times ("2 days ago") render identically across runs.

## 4. The Seed Data Structure

A robust clinical seed should use the internal `ImportService.restore_fhir_bundle` and include:
1. **Tenant & User**: A dedicated demo tenant and at least one Admin user.
2. **Patients**: At least one primary patient with a predictable MRN.
3. **Biomarkers**: `Observation` resources mapped to standard LOINC codes (the system will auto-resolve these).
4. **Medications**: `MedicationStatement` resources.
5. **Allergies**: `AllergyIntolerance` resources.
6. **Examinations**: Sideloaded via `restore_sidecar("examinations.json", ...)` to provide clinical context notes.

## 5. UI Patient Selection

By design, Admin users in Health Assistant have access to all patients in a tenant and do **not** have a default patient context automatically selected on login.
If you see "No patient selected" in the Dashboard or AI Chat:
- **During manual testing:** Use the Patient Context Switcher (usually in the header or sidebar) to select your demo patient.
- **During automated screenshots:** The E2E script (`capture.mjs`) automatically injects the primary patient into the `patient-storage` Zustand store via `addInitScript` before rendering the page.
