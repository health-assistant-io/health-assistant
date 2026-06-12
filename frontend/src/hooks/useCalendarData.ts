import { useState, useEffect, useCallback } from 'react';
import { getPatientMedications, MedicationRecord } from '../services/medicationService';
import { getExaminations } from '../services/examinationService';
import { getPatientAllergies, AllergyIntolerance } from '../services/allergyService';
import { getPatientEvents, ClinicalEvent as ClinicalEventModel } from '../services/clinicalEventService';
import { CalendarConfig, CalendarEvent } from '../types/calendar';
import { 
  adaptMedicationToEvents, 
  adaptExaminationToEvent, 
  adaptAllergyToEvent,
  adaptClinicalEventToEvents
} from '../utils/calendarUtils';
import { startOfMonth, endOfMonth, subMonths, addMonths } from 'date-fns';

export function useCalendarData(config: CalendarConfig) {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  // Destructure config to use primitives in dependency array
  const { 
    patientId, 
    startDate, 
    endDate, 
    types, 
    limitToIds, 
    examinationCategories 
  } = config;

  // Create stable strings for array/object dependencies
  const typesKey = (types || []).join(',');
  const idsKey = (limitToIds || []).join(',');
  const categoriesKey = (examinationCategories || []).join(',');
  const startKey = startDate?.getTime();
  const endKey = endDate?.getTime();

  const fetchAllData = useCallback(async () => {
    if (!patientId) {
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const rangeStart = startDate || startOfMonth(new Date());
      const rangeEnd = endDate || endOfMonth(addMonths(rangeStart, 1));
      
      const fetchPromises: Promise<any>[] = [];
      const activeTypes = types || ['medication', 'examination', 'allergy', 'clinical-event'];

      if (activeTypes.includes('medication')) {
        fetchPromises.push(getPatientMedications(patientId));
      } else {
        fetchPromises.push(Promise.resolve([]));
      }

      if (activeTypes.includes('examination')) {
        fetchPromises.push(getExaminations(patientId));
      } else {
        fetchPromises.push(Promise.resolve([]));
      }

      if (activeTypes.includes('allergy')) {
        fetchPromises.push(getPatientAllergies(patientId));
      } else {
        fetchPromises.push(Promise.resolve([]));
      }

      if (activeTypes.includes('clinical-event')) {
        fetchPromises.push(getPatientEvents(patientId));
      } else {
        fetchPromises.push(Promise.resolve([]));
      }

      const [meds, exams, allergies, clinicalEvents] = await Promise.all(fetchPromises);

      let allEvents: CalendarEvent[] = [];

      // Process Medications
      (meds as MedicationRecord[]).forEach(med => {
        // Apply limitToIds if provided
        if (limitToIds && !limitToIds.includes(med.id) && !(med.code.catalog_id && limitToIds.includes(med.code.catalog_id))) {
          return;
        }
        allEvents = [...allEvents, ...adaptMedicationToEvents(med, rangeStart, rangeEnd)];
      });

      // Process Examinations
      (exams as any[]).forEach(exam => {
        // Filter by category if needed
        if (examinationCategories && exam.category && !examinationCategories.includes(exam.category)) {
          return;
        }
        const event = adaptExaminationToEvent(exam);
        if (event) allEvents.push(event);
      });

      // Process Allergies
      (allergies as AllergyIntolerance[]).forEach(allergy => {
        const event = adaptAllergyToEvent(allergy);
        if (event) allEvents.push(event);
      });

      // Process Clinical Events
      (clinicalEvents as ClinicalEventModel[]).forEach(ce => {
        allEvents = [...allEvents, ...adaptClinicalEventToEvents(ce, rangeStart, rangeEnd)];
      });

      // Sort by date and time
      allEvents.sort((a, b) => {
        const dateDiff = a.date.getTime() - b.date.getTime();
        if (dateDiff !== 0) return dateDiff;
        return (a.time || '').localeCompare(b.time || '');
      });

      setEvents(allEvents);
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return;
      console.error('Failed to fetch calendar data:', err);
      setError(err instanceof Error ? err : new Error('Unknown error'));
    } finally {
      setLoading(false);
    }
  }, [
    patientId,
    startKey,
    endKey,
    typesKey,
    idsKey,
    categoriesKey
  ]);

  useEffect(() => {
    fetchAllData();
  }, [fetchAllData]);

  return { events, loading, error, refresh: fetchAllData };
}
