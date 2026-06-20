/**
 * Honest types for the ORM-shaped dicts the REST API actually returns.
 *
 * The backend stores FHIR-enhanced relational rows and serializes them via
 * `to_dict()` (snake_case + app-specific fields like `biomarker_id`,
 * `normalized_value`). These interfaces mirror that runtime shape — NOT the
 * FHIR R4B camelCase shape (which is only produced by the export/import path).
 */

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
}

export interface ValueQuantity {
  value: number;
  unit: string;
  system?: string;
  code?: string;
}

export interface Observation {
  id: string;
  tenant_id?: string;
  status: string;
  category?: any;
  code: {
    coding?: Array<{ system?: string; code?: string; display?: string }>;
    text?: string;
  };
  subject?: { reference: string };
  effective_datetime?: string;
  value_quantity?: ValueQuantity;
  value_string?: string;
  value_codeable_concept?: any;
  reference_range?: any;
  interpretation?: string;
  comment?: string;
  performer?: any;
  // App-specific (Biomarker Engine) fields the frontend reads
  biomarker_id?: string;
  biomarker_slug?: string;
  biomarker_info?: string;
  biomarker_aliases?: string[];
  biomarker_reference_range_min?: number | null;
  biomarker_reference_range_max?: number | null;
  raw_value?: number;
  normalized_value?: number;
  normalized_unit?: string;
  relative_score?: number;
  lab_reference_range?:
    | { min?: number; max?: number; low?: { value: number }; high?: { value: number } }
    | string
    | null;
  method?: string;
  examination_id?: string;
  document_id?: string;
}

export interface DiagnosticReport {
  id: string;
  tenant_id?: string;
  status: string;
  category?: any;
  code: {
    coding?: Array<{ system?: string; code?: string; display?: string }>;
    text?: string;
  };
  subject?: { reference: string };
  effective_datetime?: string;
  issued?: string;
  performer?: any;
  conclusion?: string;
  conclusion_code?: any;
  presented_form?: any;
}

export interface Medication {
  id: string;
  patient_id?: string;
  tenant_id?: string;
  status?: string;
  code?: { text?: string; coding?: any[]; catalog_id?: string } | any;
  start_date?: string;
  end_date?: string;
  dosage?: string;
  frequency?: any;
  reason?: string;
  note?: string;
}
