import { format, parseISO, startOfDay, addDays, isWithinInterval, addWeeks, addMonths, isSameDay } from 'date-fns';
import { MedicationRecord } from '../services/medicationService';
import { AllergyIntolerance } from '../services/allergyService';
import { ClinicalEvent as ClinicalEventModel, ClinicalEventStatus } from '../services/clinicalEventService';
import { CalendarEvent } from '../types/calendar';

/**
 * Adapts a MedicationRecord into one or more CalendarEvents based on recurrence
 */
export const adaptMedicationToEvents = (
  med: MedicationRecord,
  rangeStart: Date,
  rangeEnd: Date
): CalendarEvent[] => {
  if (!med.frequency || med.status !== 'active') {
    // Non-active medications only show on their start date if within range
    const date = med.start_date ? parseISO(med.start_date) : new Date(med.created_at || '');
    if (date >= rangeStart && date <= rangeEnd) {
      return [{
        id: `${med.id}-start`,
        type: 'medication',
        title: med.code.text,
        subtitle: `${med.dosage || ''} (Started)`,
        date: date,
        status: med.status,
        originalData: med
      }];
    }
    return [];
  }

  const occurrences: CalendarEvent[] = [];
  const medStartDate = med.start_date ? parseISO(med.start_date) : new Date(0);
  const medEndDate = med.end_date ? parseISO(med.end_date) : new Date(2100, 0, 1);
  
  const intervalStart = medStartDate > rangeStart ? medStartDate : rangeStart;
  const intervalEnd = medEndDate < rangeEnd ? medEndDate : rangeEnd;

  if (intervalStart > intervalEnd) return [];

  const timing = med.frequency;
  const times = timing.time_of_day || ['09:00'];

  for (let day = startOfDay(intervalStart); day <= intervalEnd; day = addDays(day, 1)) {
    let shouldTakeToday = false;
    
    if (timing.type === 'daily') {
      if ((timing.period || 1) <= 1) {
        shouldTakeToday = true;
      } else {
        const diffDays = Math.floor((day.getTime() - startOfDay(medStartDate).getTime()) / (1000 * 60 * 60 * 24));
        shouldTakeToday = diffDays % (timing.period || 1) === 0;
      }
    } else if (timing.type === 'interval') {
      const diffDays = Math.floor((day.getTime() - startOfDay(medStartDate).getTime()) / (1000 * 60 * 60 * 24));
      shouldTakeToday = diffDays % (timing.period || 1) === 0;
    } else if (timing.type === 'weekly' || timing.period_unit === 'week') {
      const dayName = format(day, 'eee').toLowerCase();
      if (timing.days_of_week?.includes(dayName)) {
        shouldTakeToday = true;
      }
    } else if (timing.type === 'specific_days') {
      const dayName = format(day, 'eee').toLowerCase();
      if (timing.days_of_week?.includes(dayName)) {
        shouldTakeToday = true;
      }
    }

    if (shouldTakeToday) {
      times.forEach((timeStr, idx) => {
        const [hours, minutes] = timeStr.split(':').map(Number);
        const occurrenceDate = new Date(day);
        occurrenceDate.setHours(hours, minutes, 0, 0);

        if (occurrenceDate >= medStartDate && occurrenceDate <= medEndDate) {
          occurrences.push({
            id: `${med.id}-${format(day, 'yyyyMMdd')}-${idx}`,
            type: 'medication',
            title: med.code.text,
            subtitle: med.dosage,
            date: occurrenceDate,
            time: timeStr,
            status: med.status,
            originalData: med
          });
        }
      });
    }
  }

  return occurrences;
};

/**
 * Adapts an Examination into a CalendarEvent
 */
export const adaptExaminationToEvent = (exam: any): CalendarEvent | null => {
  if (!exam.examination_date) return null;
  
  return {
    id: exam.id,
    type: 'examination',
    title: exam.category || 'Clinical Visit',
    subtitle: exam.notes || exam.impressions,
    date: parseISO(exam.examination_date),
    status: exam.extraction_status,
    category: exam.category,
    originalData: exam
  };
};

/**
 * Adapts an AllergyIntolerance into a CalendarEvent (Onset date)
 */
export const adaptAllergyToEvent = (allergy: AllergyIntolerance): CalendarEvent | null => {
  const dateStr = allergy.onset_date || allergy.last_occurrence;
  if (!dateStr) return null;

  return {
    id: allergy.id,
    type: 'allergy',
    title: `Allergy: ${allergy.code.text}`,
    subtitle: allergy.criticality,
    date: parseISO(dateStr),
    status: allergy.clinical_status,
    originalData: allergy
  };
};

/**
 * Adapts a ClinicalEventModel into one or more CalendarEvents
 */
export const adaptClinicalEventToEvents = (
  event: ClinicalEventModel,
  rangeStart: Date,
  rangeEnd: Date
): CalendarEvent[] => {
  const calendarEvents: CalendarEvent[] = [];
  
  // 1. Handle explicit occurrences
  if (event.occurrences && Array.isArray(event.occurrences)) {
    event.occurrences.forEach((occ, idx) => {
      if (!occ.date) return;
      const date = parseISO(occ.date);
      if (date >= rangeStart && date <= rangeEnd) {
        calendarEvents.push({
          id: `${event.id}-occ-${idx}`,
          type: 'clinical-event',
          title: event.title,
          subtitle: occ.notes || occ.location || event.type_details?.name,
          date: date,
          time: occ.time,
          status: event.status,
          category: event.type_details?.category_concept?.name,
          originalData: event
        });
      }
    });
  }

  // 2. Handle recurrence logic if active
  if (event.status === ClinicalEventStatus.ACTIVE && event.onset_date) {
    const onsetDate = parseISO(event.onset_date);
    const resolvedDate = event.resolved_date ? parseISO(event.resolved_date) : new Date(2100, 0, 1);
    
    const intervalStart = onsetDate > rangeStart ? onsetDate : rangeStart;
    const intervalEnd = resolvedDate < rangeEnd ? resolvedDate : rangeEnd;

    if (intervalStart <= intervalEnd) {
      const meta = event.event_metadata || {};
      const frequency = meta.frequency || 'daily'; // Default to daily as requested
      const interval = meta.interval || 1;
      const daysOfWeek = meta.days_of_week || []; // e.g. ['mon', 'wed']

      for (let day = startOfDay(intervalStart); day <= intervalEnd; day = addDays(day, 1)) {
        let shouldInclude = false;

        if (frequency === 'daily') {
          const diffDays = Math.floor((day.getTime() - startOfDay(onsetDate).getTime()) / (1000 * 60 * 60 * 24));
          shouldInclude = diffDays % interval === 0;
        } else if (frequency === 'weekly') {
          const dayName = format(day, 'eee').toLowerCase();
          if (daysOfWeek.length > 0) {
            shouldInclude = daysOfWeek.includes(dayName);
          } else {
             const diffWeeks = Math.floor((day.getTime() - startOfDay(onsetDate).getTime()) / (1000 * 60 * 60 * 24 * 7));
             shouldInclude = (diffWeeks % interval === 0) && (day.getDay() === onsetDate.getDay());
          }
        } else if (frequency === 'monthly') {
           // Simplified monthly: same day of month
           shouldInclude = day.getDate() === onsetDate.getDate();
        }

        if (shouldInclude) {
          // Avoid duplicating if an explicit occurrence already exists for this day
          const exists = calendarEvents.some(e => isSameDay(e.date, day));
          if (!exists) {
            calendarEvents.push({
              id: `${event.id}-rec-${format(day, 'yyyyMMdd')}`,
              type: 'clinical-event',
              title: event.title,
              subtitle: event.description || event.type_details?.name,
              date: day,
              time: meta.time_of_day || '12:00',
              status: event.status,
              category: event.type_details?.category_concept?.name,
              originalData: event
            });
          }
        }
      }
    }
  } else if (event.onset_date) {
    // Not active, just show the onset date
    const date = parseISO(event.onset_date);
    if (date >= rangeStart && date <= rangeEnd) {
        calendarEvents.push({
            id: `${event.id}-onset`,
            type: 'clinical-event',
            title: event.title,
            subtitle: `Started: ${event.type_details?.name || ''}`,
            date: date,
            status: event.status,
            category: event.type_details?.category_concept?.name,
            originalData: event
        });
    }
  }

  return calendarEvents;
};
