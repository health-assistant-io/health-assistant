import api from '../api/axios';

/**
 * Allergy catalog + patient-instance intolerance service.
 *
 * Mirrors `medicationService.ts`: named-function exports, strongly-typed
 * generics, axios envelope unwrapped at the service boundary. Enum values
 * are UPPERCASE to match the backend (`AllergyCategory`, `AllergyCriticality`,
 * `AllergyClinicalStatus`, `ReactionSeverity` in app/models/enums.py).
 */

// --- Enums (mirror backend app/models/enums.py — uppercase string values) ---

export type AllergyCategory = 'FOOD' | 'MEDICATION' | 'ENVIRONMENT' | 'BIOLOGIC' | 'OTHER';
export type AllergyCriticality = 'LOW' | 'HIGH' | 'UNABLE_TO_ASSESS';
export type AllergyClinicalStatus = 'ACTIVE' | 'INACTIVE' | 'RESOLVED';
export type ReactionSeverity = 'MILD' | 'MODERATE' | 'SEVERE';

// --- Catalog (reference definitions — shared across patients) ---

export interface AllergyCatalogEntry {
  id: string;
  name: string;
  category: AllergyCategory;
  description?: string | null;
  typical_reactions?: string[];
  scope?: 'system' | 'tenant' | 'user';
  class_concept_id?: string | null;
  class_concept_slug?: string | null;
  class_concept_name?: string | null;
  tenant_id?: string | null;
  created_by?: string | null;
  is_custom: boolean;
  created_at?: string;
  updated_at?: string;
}

// --- Patient-instance intolerances ---

export interface AllergyReaction {
  manifestation: string;
  severity: ReactionSeverity;
  date?: string | null;
}

export interface AllergenCode {
  text: string;
  catalog_id?: string | null;
  coding?: Array<{ system?: string; code?: string; display?: string }>;
}

export interface AllergyIntolerance {
  id: string;
  patient_id: string;
  tenant_id: string;
  clinical_status: AllergyClinicalStatus;
  verification_status?: string;
  category?: AllergyCategory | null;
  criticality?: AllergyCriticality | null;
  code: AllergenCode;
  onset_date?: string | null;
  resolved_date?: string | null;
  last_occurrence?: string | null;
  note?: string | null;
  reactions: AllergyReaction[];
  patient_name_display?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AllergyUsage {
  allergy: AllergyIntolerance;
  patient: {
    id: string;
    name: any;
    mrn?: string | null;
  };
}

// --- Payloads (write paths) ---

export type AllergyCatalogInput = Partial<AllergyCatalogEntry> & {
  name: string;
  category: AllergyCategory;
};

export type AllergyIntoleranceInput = Partial<AllergyIntolerance> & {
  clinical_status?: AllergyClinicalStatus;
  code: AllergenCode;
};

// --- Catalog methods ---

export async function searchAllergyCatalog(search?: string): Promise<AllergyCatalogEntry[]> {
  const response = await api.get<AllergyCatalogEntry[]>('/allergies/catalog', {
    params: { search },
  });
  return response.data;
}

export async function getCatalogAllergy(catalogId: string): Promise<AllergyCatalogEntry> {
  const response = await api.get<AllergyCatalogEntry>(`/allergies/catalog/${catalogId}`);
  return response.data;
}

export async function addCustomAllergen(
  data: AllergyCatalogInput,
): Promise<AllergyCatalogEntry> {
  const response = await api.post<AllergyCatalogEntry>('/allergies/catalog', data);
  return response.data;
}

export async function updateCatalogAllergy(
  catalogId: string,
  data: Partial<AllergyCatalogEntry>,
): Promise<AllergyCatalogEntry> {
  const response = await api.put<AllergyCatalogEntry>(`/allergies/catalog/${catalogId}`, data);
  return response.data;
}

export async function deleteCatalogAllergy(catalogId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/allergies/catalog/${catalogId}`);
  return response.data;
}

export async function getAllergyUsage(catalogId: string): Promise<AllergyUsage[]> {
  const response = await api.get<AllergyUsage[]>(`/allergies/catalog/${catalogId}/usage`);
  return response.data;
}

export async function reprocessAllergy(catalogId: string): Promise<AllergyCatalogEntry> {
  const response = await api.post<AllergyCatalogEntry>(`/allergies/catalog/${catalogId}/reprocess`);
  return response.data;
}

// --- Patient-instance methods ---

export async function getPatientAllergies(patientId: string): Promise<AllergyIntolerance[]> {
  const response = await api.get<AllergyIntolerance[]>(`/allergies/patient/${patientId}`);
  return response.data;
}

export async function getActiveAllergies(): Promise<AllergyIntolerance[]> {
  const response = await api.get<AllergyIntolerance[]>('/allergies/active');
  return response.data;
}

export async function getAllergy(allergyId: string): Promise<AllergyIntolerance> {
  const response = await api.get<AllergyIntolerance>(`/allergies/${allergyId}`);
  return response.data;
}

export async function addPatientAllergy(
  patientId: string,
  data: AllergyIntoleranceInput,
): Promise<AllergyIntolerance> {
  const response = await api.post<AllergyIntolerance>(`/allergies/patient/${patientId}`, data);
  return response.data;
}

export async function updatePatientAllergy(
  allergyId: string,
  data: Partial<AllergyIntolerance>,
): Promise<AllergyIntolerance> {
  const response = await api.put<AllergyIntolerance>(`/allergies/${allergyId}`, data);
  return response.data;
}

export async function deletePatientAllergy(allergyId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/allergies/${allergyId}`);
  return response.data;
}
