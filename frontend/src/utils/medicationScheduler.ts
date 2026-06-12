import { 
  parseISO, 
  startOfDay, 
  endOfDay, 
  eachDayOfInterval,
  format
} from 'date-fns';
import { MedicationRecord } from '../services/medicationService';

export interface ScheduledMedication {
  id: string;
  name: string;
  dosage?: string;
  time?: string;
  date: Date;
  record: MedicationRecord;
}

const DAY_MAP: Record<string, number> = {
  'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6
};

export function getMedicationOccurrences(
  medications: MedicationRecord[],
  startDate: Date,
  endDate: Date
): ScheduledMedication[] {
  const occurrences: ScheduledMedication[] = [];
  const interval = { start: startOfDay(startDate), end: endOfDay(endDate) };
  const days = eachDayOfInterval(interval);

  medications.forEach(med => {
    if (med.status !== 'active') return;

    const medStart = med.start_date ? startOfDay(parseISO(med.start_date)) : null;
    const medEnd = med.end_date ? endOfDay(parseISO(med.end_date)) : null;
    const timing = med.frequency;

    if (!timing) return;

    days.forEach(day => {
      // Check if day is within medication start/end range
      if (medStart && day < medStart) return;
      if (medEnd && day > medEnd) return;

      let shouldTake = false;

      switch (timing.type) {
        case 'daily':
          const freq = timing.frequency || 1;
          if (medStart) {
            const diffDays = Math.floor((day.getTime() - medStart.getTime()) / (1000 * 60 * 60 * 24));
            if (diffDays % freq === 0) shouldTake = true;
          } else {
            shouldTake = true;
          }
          break;

        case 'weekly':
          const weekFreq = timing.frequency || 1;
          if (medStart) {
            const diffWeeks = Math.floor((day.getTime() - medStart.getTime()) / (1000 * 60 * 60 * 24 * 7));
            if (diffWeeks % weekFreq === 0) {
              const currentDayName = format(day, 'eee').toLowerCase();
              if (timing.days_of_week?.includes(currentDayName)) shouldTake = true;
            }
          }
          break;

        case 'specific_days':
          const dayName = format(day, 'eee').toLowerCase();
          if (timing.days_of_week?.includes(dayName)) shouldTake = true;
          break;

        case 'interval':
          const intervalPeriod = timing.period || 1;
          const unit = timing.period_unit || 'day';
          if (medStart) {
            // Simplified interval logic
            const diffDays = Math.floor((day.getTime() - medStart.getTime()) / (1000 * 60 * 60 * 24));
            let periodInDays = intervalPeriod;
            if (unit === 'week') periodInDays *= 7;
            if (unit === 'month') periodInDays *= 30; // Approximation
            
            if (diffDays % periodInDays === 0) shouldTake = true;
          }
          break;
      }

      if (shouldTake) {
        const times = timing.time_of_day && timing.time_of_day.length > 0 
          ? timing.time_of_day 
          : ['Unspecified'];

        times.forEach(time => {
          occurrences.push({
            id: `${med.id}-${format(day, 'yyyy-MM-dd')}-${time}`,
            name: med.code.text,
            dosage: med.dosage,
            time: time,
            date: day,
            record: med
          });
        });
      }
    });
  });

  return occurrences.sort((a, b) => a.date.getTime() - b.date.getTime());
}
