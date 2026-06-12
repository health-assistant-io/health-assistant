import { addDays, addHours, format, isSameDay, parseISO, startOfDay, endOfDay, isWithinInterval } from 'date-fns';
import { MedicationRecord, MedicationTiming } from '../services/medicationService';

export interface MedicationOccurrence {
  medicationId: string;
  name: string;
  time: string; // '08:00'
  date: Date;
  dosage?: string;
  status: string;
}

export const calculateMedicationOccurrences = (
  medications: MedicationRecord[],
  startDate: Date,
  endDate: Date
): MedicationOccurrence[] => {
  const occurrences: MedicationOccurrence[] = [];

  medications.forEach(med => {
    if (!med.frequency || med.status !== 'active') return;
    
    const medStartDate = med.start_date ? parseISO(med.start_date) : new Date(0);
    const medEndDate = med.end_date ? parseISO(med.end_date) : new Date(2100, 0, 1);
    
    // Effective interval for this medication
    const intervalStart = medStartDate > startDate ? medStartDate : startDate;
    const intervalEnd = medEndDate < endDate ? medEndDate : endDate;

    if (intervalStart > intervalEnd) return;

    const timing = med.frequency;
    const times = timing.time_of_day || ['09:00'];

    // Iterate through each day in the range
    for (let day = startOfDay(intervalStart); day <= intervalEnd; day = addDays(day, 1)) {
      
      // Check if medication should be taken on this day
      let shouldTakeToday = false;
      
      if (timing.type === 'daily') {
        if ((timing.period || 1) <= 1) {
          shouldTakeToday = true;
        } else {
          const diffDays = Math.floor((day.getTime() - startOfDay(medStartDate).getTime()) / (1000 * 60 * 60 * 24));
          shouldTakeToday = diffDays % (timing.period || 1) === 0;
        }
      } else if (timing.type === 'interval') {
        if (timing.period_unit === 'hour') {
          shouldTakeToday = true; // Recurring within the day
        } else {
          const diffDays = Math.floor((day.getTime() - startOfDay(medStartDate).getTime()) / (1000 * 60 * 60 * 24));
          shouldTakeToday = diffDays % (timing.period || 1) === 0;
        }
      } else if (timing.type === 'weekly' || timing.period_unit === 'week') {
        const dayName = format(day, 'eee').toLowerCase(); // 'mon', 'tue'...
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
        times.forEach(timeStr => {
          const [hours, minutes] = timeStr.split(':').map(Number);
          const occurrenceDate = new Date(day);
          occurrenceDate.setHours(hours, minutes, 0, 0);

          // Check if within medication duration
          if (occurrenceDate >= medStartDate && occurrenceDate <= medEndDate) {
            occurrences.push({
              medicationId: med.id,
              name: med.code.text,
              time: timeStr,
              date: occurrenceDate,
              dosage: med.dosage,
              status: med.status
            });
          }
        });
      }
    }
  });

  return occurrences.sort((a, b) => a.date.getTime() - b.date.getTime());
};
