/**
 * Observation type — mirrors the ORM-shape `to_dict()` output returned by
 * the /observations/* domain endpoints. NOT canonical FHIR R4.
 *
 * The backend stores FHIR-enhanced relational rows and serializes them via
 * `to_dict()` (snake_case + app-specific Biomarker Engine fields like
 * `biomarker_id`, `normalized_value`, `relative_score`). This interface
 * mirrors that runtime shape — NOT the FHIR R4B camelCase shape (which is
 * only produced by the /fhir/R4/* facade and the export/import path).
 */

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
