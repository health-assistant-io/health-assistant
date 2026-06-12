import api from '../api/axios';

export interface MedicationCatalogEntry {
  id: string;
  name: string;
  description?: string;
  indications?: string;
  side_effects: string[];
  contraindications?: string;
  dosage_info?: string;
  is_custom: boolean;
}

export interface MedicationTiming {
  type: 'daily' | 'weekly' | 'specific_days' | 'interval';
  frequency?: number;
  period?: number;
  period_unit?: 'hour' | 'day' | 'week' | 'month';
  days_of_week?: string[]; // ['mon', 'tue', ...]
  time_of_day?: string[]; // ['08:00', '20:00']
  as_needed?: boolean;
  display?: string;
}

export interface MedicationRecord {
  id: string;
  patient_id: string;
  tenant_id: string;
  examination_id?: string;
  status: 'active' | 'completed' | 'entered-in-error' | 'intended' | 'stopped' | 'on-hold' | 'unknown';
  code: {
    text: string;
    catalog_id?: string;
  };
  start_date?: string;
  end_date?: string;
  dosage?: string;
  frequency?: MedicationTiming;
  reason?: string;
  note?: string;
  created_at: string;
  updated_at?: string;
}

export async function searchMedicationCatalog(search?: string): Promise<MedicationCatalogEntry[]> {
  const response = await api.get<MedicationCatalogEntry[]>('/medications/catalog', {
    params: { search }
  });
  return response.data;
}

export async function getCatalogMedication(catalogId: string): Promise<MedicationCatalogEntry> {
  const response = await api.get<MedicationCatalogEntry>(`/medications/catalog/${catalogId}`);
  return response.data;
}

export async function addCustomMedication(data: Partial<MedicationCatalogEntry>): Promise<MedicationCatalogEntry> {
  const response = await api.post<MedicationCatalogEntry>('/medications/catalog', data);
  return response.data;
}

export async function updateCatalogMedication(catalogId: string, data: Partial<MedicationCatalogEntry>): Promise<MedicationCatalogEntry> {
  const response = await api.put<MedicationCatalogEntry>(`/medications/catalog/${catalogId}`, data);
  return response.data;
}

export async function getPatientMedications(patientId: string): Promise<MedicationRecord[]> {
  const response = await api.get<MedicationRecord[]>(`/medications/patient/${patientId}`);
  return response.data;
}

export async function addPatientMedication(patientId: string, data: Partial<MedicationRecord>): Promise<MedicationRecord> {
  // Map our internal timing to FHIR timing
  const fhirData = {
    ...data,
    patient_id: patientId,
    subject: { reference: `Patient/${patientId}` },
    timing: data.frequency ? {
      repeat: {
        frequency: data.frequency.frequency,
        period: data.frequency.period,
        periodUnit: data.frequency.period_unit === 'day' ? 'd' : (data.frequency.period_unit === 'week' ? 'wk' : 'mo'),
        dayOfWeek: data.frequency.days_of_week,
        timeOfDay: data.frequency.time_of_day
      }
    } : undefined
  };
  const response = await api.post<MedicationRecord>(`/fhir/Medication`, fhirData);
  return response.data;
}

export async function updatePatientMedication(medicationId: string, data: Partial<MedicationRecord>): Promise<MedicationRecord> {
  const response = await api.put<MedicationRecord>(`/medications/${medicationId}`, data);
  return response.data;
}

export async function deletePatientMedication(medicationId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/medications/${medicationId}`);
  return response.data;
}

export interface MedicationUsage {
  medication: MedicationRecord;
  patient: {
    id: string;
    name: any;
    mrn?: string;
  };
}

export async function getMedicationUsage(catalogId: string): Promise<MedicationUsage[]> {
  const response = await api.get<MedicationUsage[]>(`/medications/catalog/${catalogId}/usage`);
  return response.data;
}

export async function reprocessMedication(catalogId: string): Promise<MedicationCatalogEntry> {
  const response = await api.post<MedicationCatalogEntry>(`/medications/catalog/${catalogId}/reprocess`);
  return response.data;
}
