/**
 * Types for vaccines & patient immunizations (mirrors backend schemas).
 */

export type ImmunizationStatus = 'completed' | 'entered-in-error' | 'not-done';

export interface VaccineCodeableConcept {
  text: string;
  coding?: { system?: string; code?: string; display?: string }[];
  catalog_id?: string | null;
}

export interface VaccineCatalogEntry {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  coding_system?: string | null;
  code?: string | null;
  target_diseases?: string[] | null;
  dose_schedule?: Record<string, unknown> | null;
  contraindications?: string | null;
  side_effects?: string[] | null;
  is_custom: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PatientImmunization {
  id: string;
  patient_id: string;
  vaccine_catalog_id?: string | null;
  examination_id?: string | null;
  status: ImmunizationStatus;
  vaccine_code: VaccineCodeableConcept;
  administered_at?: string | null;
  dose_number?: string | null;
  lot_number?: string | null;
  manufacturer?: string | null;
  location?: string | null;
  note?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PatientImmunizationCreate {
  vaccine_catalog_id?: string | null;
  examination_id?: string | null;
  status?: ImmunizationStatus;
  vaccine_code: VaccineCodeableConcept;
  administered_at?: string | null;
  dose_number?: string | null;
  lot_number?: string | null;
  manufacturer?: string | null;
  location?: string | null;
  note?: string | null;
}

export interface PatientImmunizationUpdate {
  examination_id?: string | null;
  status?: ImmunizationStatus;
  vaccine_code?: VaccineCodeableConcept;
  administered_at?: string | null;
  dose_number?: string | null;
  lot_number?: string | null;
  manufacturer?: string | null;
  location?: string | null;
  note?: string | null;
}
