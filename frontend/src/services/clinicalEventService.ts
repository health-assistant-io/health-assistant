import api from '../api/axios';

export interface ClinicalEventCategory {
  id: string;
  name: string;
  slug: string;
  description?: string;
  icon?: { type: string; value: string };
  color?: string;
  tenant_id?: string;
}

export interface ClinicalEventType {
  id: string;
  name: string;
  slug: string;
  description?: string;
  icon?: { type: string; value: string };
  color?: string;
  metadata_schema?: Record<string, any>;
  category_concept_id?: string;
  category_concept?: ClinicalEventCategory;
}

export enum ClinicalEventStatus {
  ACTIVE = 'ACTIVE',
  RESOLVED = 'RESOLVED',
  ON_HOLD = 'ON_HOLD',
  UNKNOWN = 'UNKNOWN',
}

export interface ClinicalEvent {
  id: string;
  patient_id: string;
  tenant_id: string;
  type_id?: string;
  type_details?: ClinicalEventType;
  status: ClinicalEventStatus;
  title: string;
  description?: string;
  onset_date?: string;
  resolved_date?: string;
  occurrences: any[];
  event_metadata: Record<string, any>;
  coding_system?: 'loinc' | 'snomed' | 'custom';
  code?: string;
  examinations: any[];
  observations: any[];
  created_at: string;
  updated_at: string;
}

export const getEventCategories = async (): Promise<ClinicalEventCategory[]> => {
  const response = await api.get('/concepts?kind=event_category&limit=500');
  return response.data;
};

export const createEventCategory = async (categoryData: any): Promise<ClinicalEventCategory> => {
  const response = await api.post('/concepts', { ...categoryData, kind: 'event_category' });
  return response.data;
};

export const getEventTypes = async (): Promise<ClinicalEventType[]> => {
  const response = await api.get('/clinical-events/types');
  return response.data;
};

export const createEventType = async (typeData: any): Promise<ClinicalEventType> => {
  const response = await api.post('/clinical-events/types', typeData);
  return response.data;
};

export const getPatientEvents = async (patientId: string): Promise<ClinicalEvent[]> => {
  const response = await api.get(`/clinical-events?patient_id=${patientId}`);
  return response.data;
};

export const getExaminationEvents = async (examinationId: string): Promise<ClinicalEvent[]> => {
  const response = await api.get(`/clinical-events?examination_id=${examinationId}`);
  return response.data;
};

export const createEvent = async (eventData: any): Promise<ClinicalEvent> => {
  const response = await api.post('/clinical-events', eventData);
  return response.data;
};

export const updateEvent = async (eventId: string, eventData: any): Promise<ClinicalEvent> => {
  const response = await api.put(`/clinical-events/${eventId}`, eventData);
  return response.data;
};

export const getEvent = async (eventId: string): Promise<ClinicalEvent> => {
  const response = await api.get(`/clinical-events/${eventId}`);
  return response.data;
};

export const deleteEvent = async (eventId: string): Promise<void> => {
  await api.delete(`/clinical-events/${eventId}`);
};

export const linkExaminationToEvent = async (eventId: string, examinationId: string, reason?: string): Promise<ClinicalEvent> => {
  const response = await api.post(`/clinical-events/${eventId}/link-examination`, {
    examination_id: examinationId,
    reason,
  });
  return response.data;
};
