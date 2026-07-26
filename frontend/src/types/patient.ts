/**
 * Patient type — mirrors the ORM-shape `to_dict()` output returned by
 * the /patients/* domain endpoints. NOT canonical FHIR R4.
 *
 * The backend stores FHIR-enhanced relational rows and serializes them via
 * `to_dict()` (snake_case + app-specific fields like `user_id`, `mrn`,
 * `dashboard_layout`). This interface mirrors that runtime shape — NOT the
 * FHIR R4B camelCase shape (which is only produced by the /fhir/R4/* facade
 * and the export/import path).
 */

export interface OmbCategory {
  system?: string;
  code: string;
  display?: string;
}

/**
 * Local-keyed FHIR R4 extensions map stored on `Patient.extensions`
 * (validated against the backend registry in `fhir_extensions.py`).
 * Mirrors the shape the backend stores + returns via `to_dict()`.
 */
export interface PatientExtensions {
  race?: { ombCategory?: OmbCategory; text?: string };
  ethnicity?: { ombCategory?: OmbCategory; text?: string };
  /** ISO/BCP-47 language code, e.g. 'en', 'el'. */
  preferred_language?: string;
  insurance_provider?: string;
}

export interface Patient {
  id: string;
  tenant_id?: string;
  user_id?: string | null;
  name: { family: string; given: string[] } | { text: string } | any;
  gender: string;
  birth_date?: string;
  age?: number | null;
  deceased_boolean?: boolean | null;
  deceased_datetime?: string | null;
  address?: any;
  telecom?: any;
  mrn?: string;
  emergency_contact?: any;
  dashboard_layout?: any;
  /** FHIR R4 extensions (race / ethnicity / preferred_language / insurance_provider). */
  extensions?: PatientExtensions | null;
}
