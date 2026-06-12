import api from '../api/axios';

export interface AllergyCatalogEntry {
  id: string;
  name: string;
  category: 'food' | 'medication' | 'environment' | 'biologic';
  description?: string;
  typical_reactions?: string[];
  is_custom: boolean;
}

export interface AllergyIntolerance {
  id: string;
  patient_id: string;
  clinical_status: 'active' | 'inactive' | 'resolved';
  verification_status: string;
  category?: 'food' | 'medication' | 'environment' | 'biologic';
  criticality?: 'low' | 'high' | 'unable-to-assess';
  code: {
    text: string;
    catalog_id?: string;
  };
  onset_date?: string;
  resolved_date?: string;
  last_occurrence?: string;
  note?: string;
  reactions: Array<{
    manifestation: string;
    severity: 'mild' | 'moderate' | 'severe';
    date?: string;
  }>;
}

export async function searchAllergyCatalog(search?: string): Promise<AllergyCatalogEntry[]> {
  const response = await api.get<AllergyCatalogEntry[]>('/allergies/catalog', {
    params: { search }
  });
  return response.data;
}

export async function addCustomAllergen(name: string, category: string, description?: string): Promise<AllergyCatalogEntry> {
  const response = await api.post<AllergyCatalogEntry>('/allergies/catalog', {
    name, category, description
  });
  return response.data;
}

export async function getPatientAllergies(patientId: string): Promise<AllergyIntolerance[]> {
  const response = await api.get<AllergyIntolerance[]>(`/allergies/patient/${patientId}`);
  return response.data;
}

export async function getActiveAllergies(): Promise<AllergyIntolerance[]> {
  const response = await api.get<AllergyIntolerance[]>('/allergies/active');
  return response.data;
}

export async function addPatientAllergy(patientId: string, data: Partial<AllergyIntolerance>): Promise<AllergyIntolerance> {
  const response = await api.post<AllergyIntolerance>(`/allergies/patient/${patientId}`, data);
  return response.data;
}

export async function updatePatientAllergy(allergyId: string, data: Partial<AllergyIntolerance>): Promise<AllergyIntolerance> {
  const response = await api.put<AllergyIntolerance>(`/allergies/${allergyId}`, data);
  return response.data;
}

export async function deletePatientAllergy(allergyId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/allergies/${allergyId}`);
  return response.data;
}
