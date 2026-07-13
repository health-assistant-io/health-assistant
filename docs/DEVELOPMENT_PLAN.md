# Health Assistant — Development Roadmap

The roadmap for Health Assistant — upcoming features for the self-hosted, open-source health records platform. See [STATUS.md](STATUS.md) for what's already shipped.

## Upcoming Feature: Biomarker Clinical Insights & Correlations
### Goal
Enhance the biomarker system to provide deeper clinical insights, better categorization, and correlations with patient symptoms (e.g., organ pain).

### Implementation Steps

#### 1. Database Updates
- **`BiomarkerDefinition` Enhancements**:
  - `clinical_scope` (Text): Explanation of what the biomarker measures.
  - `high_implication` (Text): Medical implications of elevated levels.
  - `low_implication` (Text): Medical implications of depressed levels.
- **`BiomarkerCorrelation` Model**:
  - Link biomarkers to `BodyPartModel` (Organs).
  - Link biomarkers to `ClinicalEventType` (Symptoms like pain, fatigue).

#### 2. Backend & API Updates
- Update schemas (`BiomarkerCreate`, `BiomarkerUpdate`, `BiomarkerResponse`) to handle new fields.
- Add endpoint `/api/v1/biomarkers/correlated` for querying by organ/symptom.
- Update Alembic migrations.

#### 3. Frontend Enhancements
- Expand biomarker detail UI with a tabbed interface (Scope, High/Low Implications, Related Symptoms).
- Implement contextual filtering (e.g., filter biomarkers by Body System or associated symptom).
- Suggest relevant biomarkers when a user logs a specific clinical event (e.g., "Liver Pain" -> suggests checking AST/ALT).

#### 4. Automation & Testing
- Write `scripts/backfill_biomarker_insights.py` to auto-generate missing scope/implications using the AI service.
- Expand `test_biomarkers.py` to cover new correlation logic.
- Update all documentation markdown files to reflect new features.

## New Feature: Biomarker-Clinical Event Binding & Ophthalmic Support
### Goal
Extend the system to support specialized medical domains like Ophthalmology by linking chronic conditions (Clinical Events) with quantitative measurements (Biomarkers).

### Implementation Details
#### 1. Database Model Additions
- **`BiomarkerEventCorrelation`**: Maps `BiomarkerDefinition` to `ClinicalEventType` (Conceptual binding).
- **`EventObservationLink`**: Maps specific `ClinicalEvent` instances to `Observation` records (Instance binding).

#### 2. Ophthalmic Catalog Seeding
- **New Biomarkers**: Visual Acuity (OD/OS), Intraocular Pressure (OD/OS), Refraction Sphere/Cylinder/Axis.
- **New Units**: Diopters (D), Degrees (°).
- **New Event Types**: Refractive Error (Myopia, Presbyopia), Cataract, Glaucoma Suspect.
- **Correlations**: Link "Refractive Error" to "Visual Acuity" and "Refraction" biomarkers.

#### 3. API & Extraction Logic
- Update AI extraction to map ocular findings to the new structures.
- Add endpoints to retrieve correlated biomarkers for a given clinical event.
