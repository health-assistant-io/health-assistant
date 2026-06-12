import { MedicationRecord } from '../services/medicationService';
import { AllergyIntolerance } from '../services/allergyService';
import { ClinicalEvent as ClinicalEventModel } from '../services/clinicalEventService';

export type CalendarEventType = 'medication' | 'examination' | 'allergy' | 'clinical-event' | 'custom';

export interface CalendarEvent {
  id: string;
  type: CalendarEventType;
  title: string;
  subtitle?: string;
  date: Date;
  time?: string;
  status?: string;
  category?: string;
  originalData?: MedicationRecord | AllergyIntolerance | ClinicalEventModel | any;
}

export interface CalendarConfig {
  patientId?: string;
  types?: CalendarEventType[];
  limitToIds?: string[];
  examinationCategories?: string[];
  startDate?: Date;
  endDate?: Date;
}
