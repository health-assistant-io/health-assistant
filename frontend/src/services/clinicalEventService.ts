import api from '../api/axios';
import type { MetadataSchema } from '../types/metadataSchema';

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
  /** Typed descriptor driving DynamicMetadataForm. Backend-validated. */
  metadata_schema?: MetadataSchema;
  // Phase 4a behavior-driving template fields.
  severity_scale?: Record<string, any>;
  phases?: Array<Record<string, any>>;
  milestones?: Array<Record<string, any>>;
  default_duration_days?: number;
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
  // Phase 3b: structured anatomy links (EventAnatomyLink).
  anatomy_links?: Array<{
    id: string;
    anatomy_id: string;
    name?: string;
    relation_type?: string;
  }>;
  created_at: string;
  updated_at: string;
}

export interface ClinicalEventOccurrencePayload {
  occurred_at: string;
  title?: string;
  severity?: string;
  intensity?: number;
  notes?: string;
  anatomy_id?: string;
  metadata?: Record<string, any>;
}

export interface JourneyInsights {
  current_phase?: Record<string, any> | null;
  upcoming_milestones: any[];
  overdue_milestones: any[];
  recommended_biomarkers: any[];
  is_overdue: boolean;
  days_since_onset?: number | null;
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

// Phase 2: closes the asymmetry with link-examination.
export const linkObservationToEvent = async (eventId: string, observationId: string, notes?: string): Promise<ClinicalEvent> => {
  const response = await api.post(`/clinical-events/${eventId}/link-observation`, {
    observation_id: observationId,
    notes,
  });
  return response.data;
};

// Phase 3a: discrete journey episodes.
export const addOccurrence = async (eventId: string, payload: ClinicalEventOccurrencePayload): Promise<ClinicalEvent> => {
  const response = await api.post(`/clinical-events/${eventId}/occurrences`, payload);
  return response.data;
};

export const deleteOccurrence = async (eventId: string, occurrenceId: string): Promise<ClinicalEvent> => {
  const response = await api.delete(`/clinical-events/${eventId}/occurrences/${occurrenceId}`);
  return response.data;
};

// Phase 3b: structured anatomy links.
export const linkAnatomy = async (eventId: string, anatomyId: string, relationType: string = 'primary_site'): Promise<ClinicalEvent> => {
  const response = await api.post(`/clinical-events/${eventId}/link-anatomy`, {
    anatomy_id: anatomyId,
    relation_type: relationType,
  });
  return response.data;
};

export const unlinkAnatomy = async (eventId: string, anatomyId: string): Promise<ClinicalEvent> => {
  const response = await api.delete(`/clinical-events/${eventId}/unlink-anatomy/${anatomyId}`);
  return response.data;
};

// Phase 4a: type-driven journey insights.
export const getEventInsights = async (eventId: string): Promise<JourneyInsights> => {
  const response = await api.get(`/clinical-events/${eventId}/insights`);
  return response.data;
};

// Phase 4b: biomarker ↔ event-type correlations.
export const addCorrelatedBiomarker = async (
  typeId: string,
  biomarkerId: string,
  correlationType: string = 'monitoring',
  description?: string,
): Promise<any> => {
  const response = await api.post(`/clinical-events/types/${typeId}/biomarkers`, {
    biomarker_id: biomarkerId,
    correlation_type: correlationType,
    description,
  });
  return response.data;
};

export const removeCorrelatedBiomarker = async (typeId: string, biomarkerId: string): Promise<void> => {
  await api.delete(`/clinical-events/types/${typeId}/biomarkers/${biomarkerId}`);
};
