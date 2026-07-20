import { MedicationRecord } from '../services/medicationService';
import { AllergyIntolerance } from '../services/allergyService';
import { ClinicalEvent as ClinicalEventModel } from '../services/clinicalEventService';

export type CalendarEventType = 'medication' | 'examination' | 'allergy' | 'clinical-event' | 'custom';

/**
 * Discriminator that tells consumers how to render an event.
 *
 * - `point`  — a discrete timestamp (exam visit, medication dose, allergy
 *   onset, clinical-event occurrence, single-day clinical-event onset).
 *   Rendered as one card on `date`.
 * - `range`  — a bounded span with both `date` (start) and `endDate` set
 *   (e.g. a resolved clinical event with onset + resolved dates).
 *   Consumers should render compactly; never one card per day.
 * - `state`  — an ongoing span with `date` (onset) but no `endDate`
 *   (e.g. `status=ACTIVE` clinical event with no `resolved_date`).
 *   Consumers must render exactly once; never expand per day.
 */
export type CalendarEventKind = 'point' | 'range' | 'state';

export interface CalendarEvent {
  id: string;
  type: CalendarEventType;
  title: string;
  subtitle?: string;
  date: Date;
  /** End date for `range`/`state` events; undefined for `point` events. */
  endDate?: Date;
  time?: string;
  status?: string;
  category?: string;
  /** Defaults to `'point'`. Adapters for medications/exams/allergies always emit `point`. */
  kind?: CalendarEventKind;
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
