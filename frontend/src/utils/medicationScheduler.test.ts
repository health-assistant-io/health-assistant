import { describe, it, expect } from 'vitest';
import { getMedicationOccurrences } from './medicationScheduler';
import { MedicationRecord } from '../services/medicationService';
import { parseISO } from 'date-fns';

describe('medicationScheduler', () => {
  const mockMeds: MedicationRecord[] = [
    {
      id: 'med-1',
      patient_id: 'p-1',
      tenant_id: 't-1',
      status: 'active',
      code: { text: 'Aspirin' },
      start_date: '2026-03-01',
      dosage: '100mg',
      frequency: {
        type: 'daily',
        frequency: 1,
        time_of_day: ['08:00', '20:00']
      },
      created_at: '2026-01-01'
    },
    {
      id: 'med-2',
      patient_id: 'p-1',
      tenant_id: 't-1',
      status: 'active',
      code: { text: 'Ibuprofen' },
      start_date: '2026-03-01',
      dosage: '200mg',
      frequency: {
        type: 'specific_days',
        days_of_week: ['mon', 'wed', 'fri'],
        time_of_day: ['12:00']
      },
      created_at: '2026-01-01'
    }
  ];

  it('should calculate daily occurrences correctly', () => {
    const start = parseISO('2026-03-01');
    const end = parseISO('2026-03-02');
    const result = getMedicationOccurrences(mockMeds.slice(0, 1), start, end);
    
    // 2 occurrences per day * 2 days = 4
    expect(result.length).toBe(4);
    expect(result[0].name).toBe('Aspirin');
    expect(result[0].time).toBe('08:00');
  });

  it('should filter by specific days correctly', () => {
    const start = parseISO('2026-03-02'); // Monday
    const end = parseISO('2026-03-02');
    const result = getMedicationOccurrences(mockMeds.slice(1, 2), start, end);
    
    expect(result.length).toBe(1);
    expect(result[0].name).toBe('Ibuprofen');
    
    const tuesday = parseISO('2026-03-03');
    const resultTuesday = getMedicationOccurrences(mockMeds.slice(1, 2), tuesday, tuesday);
    expect(resultTuesday.length).toBe(0);
  });

  it('should respect medication end date', () => {
    const medWithEnd: MedicationRecord = {
      ...mockMeds[0],
      end_date: '2026-03-05'
    };
    const start = parseISO('2026-03-06');
    const end = parseISO('2026-03-07');
    const result = getMedicationOccurrences([medWithEnd], start, end);
    
    expect(result.length).toBe(0);
  });
});
