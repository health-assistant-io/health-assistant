import { describe, it, expect } from 'vitest';
import { parseISO, startOfMonth, endOfMonth } from 'date-fns';
import {
  adaptClinicalEventToEvents,
  adaptMedicationToEvents,
  adaptExaminationToEvent,
  adaptAllergyToEvent,
  getActiveConditions,
  groupActiveConditionsByCategory,
} from './calendarUtils';
import { ClinicalEvent, ClinicalEventStatus, ScheduleKind, RecurrenceFrequency } from '../services/clinicalEventService';
import { MedicationRecord } from '../services/medicationService';
import { CalendarEvent } from '../types/calendar';
// Visible range used across tests: March 2026.
const RANGE_START = startOfMonth(parseISO('2026-03-01'));
const RANGE_END = endOfMonth(parseISO('2026-03-31'));

function makeEvent(overrides: Partial<ClinicalEvent>): ClinicalEvent {
  return {
    id: 'evt-1',
    patient_id: 'p-1',
    tenant_id: 't-1',
    status: ClinicalEventStatus.ACTIVE,
    title: 'Back Pain',
    occurrences: [],
    event_metadata: {},
    examinations: [],
    observations: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } as ClinicalEvent;
}

describe('adaptClinicalEventToEvents — kind-aware behavior', () => {
  describe('state (ongoing, no resolved_date, no recurrence)', () => {
    it('emits exactly ONE event on onset, never per-day', () => {
      const event = makeEvent({
        id: 'pain-1',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-02-15', // before range
        event_metadata: {},
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(1);
      expect(events[0].kind).toBe('state');
      expect(events[0].id).toBe('pain-1-state');
      expect(events[0].date).toEqual(parseISO('2026-02-15'));
      expect(events[0].endDate).toBeUndefined();
    });

    it('emits the single state event even when onset is inside the visible range', () => {
      const event = makeEvent({
        id: 'pain-2',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-03-10',
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(1);
      expect(events[0].kind).toBe('state');
    });

    it('does NOT duplicate across a wide range (the bloat bug)', () => {
      const wideStart = startOfMonth(parseISO('2026-01-01'));
      const wideEnd = endOfMonth(parseISO('2026-06-30'));
      const event = makeEvent({
        id: 'pain-3',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-01-15',
      });
      const events = adaptClinicalEventToEvents(event, wideStart, wideEnd);
      // Six months would previously have produced ~167 daily cards.
      expect(events).toHaveLength(1);
    });

    it('emits nothing when onset is missing', () => {
      const event = makeEvent({
        status: ClinicalEventStatus.ACTIVE,
        onset_date: undefined,
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(0);
    });
  });

  describe('range (onset + resolved_date, no recurrence)', () => {
    it('emits one event on onset with endDate set', () => {
      const event = makeEvent({
        id: 'flu-1',
        status: ClinicalEventStatus.RESOLVED,
        onset_date: '2026-03-05',
        resolved_date: '2026-03-12',
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(1);
      expect(events[0].kind).toBe('range');
      expect(events[0].date).toEqual(parseISO('2026-03-05'));
      expect(events[0].endDate).toEqual(parseISO('2026-03-12'));
    });
  });

  describe('point (non-active status, no resolved_date, no recurrence)', () => {
    it('emits one point event on onset for RESOLVED status without resolved_date', () => {
      const event = makeEvent({
        id: 'incident-1',
        status: ClinicalEventStatus.RESOLVED,
        onset_date: '2026-03-10',
        resolved_date: undefined,
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(1);
      expect(events[0].kind).toBe('point');
      expect(events[0].id).toBe('incident-1-onset');
    });

    it('emits point event for ON_HOLD status', () => {
      const event = makeEvent({
        status: ClinicalEventStatus.ON_HOLD,
        onset_date: '2026-03-10',
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(1);
      expect(events[0].kind).toBe('point');
    });
  });

  describe('recurring (explicit event_metadata.frequency)', () => {
    it('expands daily when frequency is EXPLICITLY daily', () => {
      const event = makeEvent({
        id: 'physio-1',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-03-01',
        event_metadata: { frequency: 'daily', interval: 1 },
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events.length).toBe(31); // every day in March
      events.forEach(e => expect(e.kind).toBe('point'));
      expect(events[0].id).toMatch(/physio-1-rec-\d{8}/);
    });

    it('does NOT expand when frequency is missing (default is no expansion)', () => {
      const event = makeEvent({
        id: 'pain-no-freq',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-03-01',
        event_metadata: {}, // <-- the fix: no frequency key
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(1);
      expect(events[0].kind).toBe('state');
    });

    it('respects weekly frequency with days_of_week', () => {
      const event = makeEvent({
        id: 'checkup-1',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-03-02', // Monday
        event_metadata: { frequency: 'weekly', days_of_week: ['mon', 'wed'] },
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      // March 2026: Mondays = 2, 9, 16, 23, 30 (5); Wednesdays = 4, 11, 18, 25 (4) → 9
      expect(events).toHaveLength(9);
    });

    it('respects resolved_date as upper bound on recurrence', () => {
      const event = makeEvent({
        id: 'short-rec',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-03-01',
        resolved_date: '2026-03-05',
        event_metadata: { frequency: 'daily', interval: 1 },
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(5);
    });
  });

  describe('explicit occurrences[]', () => {
    it('emits each occurrence as a point event in addition to the state event', () => {
      const event = makeEvent({
        id: 'mixed-1',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-03-01',
        occurrences: [
          { date: '2026-03-10', intensity: 7, notes: 'flare-up' },
          { date: '2026-03-20', intensity: 5 },
        ],
        event_metadata: {},
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      // 1 state event (onset) + 2 explicit occurrences
      expect(events).toHaveLength(3);
      const occurrenceEvents = events.filter(e => e.id.includes('-occ-'));
      expect(occurrenceEvents).toHaveLength(2);
      occurrenceEvents.forEach(e => expect(e.kind).toBe('point'));
    });

    it('dedupes recurrence against explicit occurrences on the same day', () => {
      const event = makeEvent({
        id: 'dedup-1',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-03-01',
        occurrences: [{ date: '2026-03-01' }],
        event_metadata: { frequency: 'daily', interval: 1 },
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      // 2026-03-01 should appear once, not twice
      const mar1 = events.filter(e =>
        e.date.getTime() === parseISO('2026-03-01').getTime()
      );
      expect(mar1).toHaveLength(1);
    });
  });

  describe('out-of-range onset', () => {
    it('still emits state event when onset is outside the range (so consumers can surface currently-active items)', () => {
      const event = makeEvent({
        id: 'past-1',
        status: ClinicalEventStatus.ACTIVE,
        onset_date: '2026-01-15', // before the March window
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(1);
      expect(events[0].kind).toBe('state');
    });

    it('filters out point events when onset is outside the range', () => {
      const event = makeEvent({
        id: 'future-point',
        status: ClinicalEventStatus.RESOLVED,
        onset_date: '2026-05-15',
        resolved_date: undefined,
      });
      const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
      expect(events).toHaveLength(0);
    });
  });
});

describe('adaptMedicationToEvents', () => {
  it('emits point events only', () => {
    const med: MedicationRecord = {
      id: 'med-1',
      patient_id: 'p-1',
      tenant_id: 't-1',
      status: 'active',
      code: { text: 'Aspirin' },
      start_date: '2026-03-01',
      dosage: '100mg',
      frequency: { type: 'daily', frequency: 1, time_of_day: ['08:00'] },
      created_at: '2026-01-01',
    } as MedicationRecord;
    const events = adaptMedicationToEvents(med, RANGE_START, RANGE_END);
    expect(events.length).toBeGreaterThan(0);
    events.forEach(e => expect(e.kind).toBe('point'));
  });

  it('non-active med emits a single point event on start_date', () => {
    const med: MedicationRecord = {
      id: 'med-2',
      patient_id: 'p-1',
      tenant_id: 't-1',
      status: 'stopped',
      code: { text: 'Old Drug' },
      start_date: '2026-03-05',
      dosage: '50mg',
      created_at: '2026-01-01',
    } as MedicationRecord;
    const events = adaptMedicationToEvents(med, RANGE_START, RANGE_END);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('point');
  });
});

describe('adaptExaminationToEvent', () => {
  it('returns a point event', () => {
    const event = adaptExaminationToEvent({
      id: 'exam-1',
      examination_date: '2026-03-10T10:00:00Z',
      category: 'Cardiology',
    });
    expect(event).not.toBeNull();
    expect(event!.kind).toBe('point');
  });

  it('returns null when examination_date is missing', () => {
    expect(adaptExaminationToEvent({ id: 'exam-2' })).toBeNull();
  });
});

describe('adaptAllergyToEvent', () => {
  it('returns a point event', () => {
    const event = adaptAllergyToEvent({
      id: 'allergy-1',
      code: { text: 'Peanuts' },
      onset_date: '2026-03-10',
      criticality: 'high',
      clinical_status: 'active',
    } as any);
    expect(event).not.toBeNull();
    expect(event!.kind).toBe('point');
  });
});

// ---------------------------------------------------------------------------
// getActiveConditions / groupActiveConditionsByCategory
// ---------------------------------------------------------------------------

const NOW = parseISO('2026-04-15');

function makeCalEvent(partial: Partial<CalendarEvent>): CalendarEvent {
  return {
    id: 'x',
    type: 'clinical-event',
    title: 'X',
    date: new Date(),
    ...partial,
  } as CalendarEvent;
}

describe('adaptClinicalEventToEvents — schedule_kind (Phase 4) priority', () => {
  it('honors schedule_kind="state" regardless of status/resolved_date', () => {
    // RESOLVED status with resolved_date set — but the type declared "state".
    const event = makeEvent({
      id: 'forced-state',
      status: ClinicalEventStatus.RESOLVED,
      onset_date: '2026-03-01',
      resolved_date: '2026-03-10',
      schedule_kind: ScheduleKind.STATE,
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('state');
    expect(events[0].endDate).toBeUndefined();
  });

  it('honors schedule_kind="range" — emits endDate from resolved_date', () => {
    const event = makeEvent({
      id: 'forced-range',
      status: ClinicalEventStatus.ACTIVE,
      onset_date: '2026-03-01',
      resolved_date: '2026-03-10',
      schedule_kind: ScheduleKind.RANGE,
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('range');
    expect(events[0].endDate).toEqual(parseISO('2026-03-10'));
  });

  it('honors schedule_kind="range" even without resolved_date (open-ended range)', () => {
    const event = makeEvent({
      id: 'range-no-end',
      status: ClinicalEventStatus.ACTIVE,
      onset_date: '2026-03-01',
      schedule_kind: ScheduleKind.RANGE,
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('range');
    expect(events[0].endDate).toBeUndefined();
  });

  it('honors schedule_kind="point" — filters by visible range', () => {
    const inRange = makeEvent({
      id: 'forced-point-in',
      onset_date: '2026-03-15',
      schedule_kind: ScheduleKind.POINT,
    });
    const outOfRange = makeEvent({
      id: 'forced-point-out',
      onset_date: '2026-05-15',
      schedule_kind: ScheduleKind.POINT,
    });
    expect(adaptClinicalEventToEvents(inRange, RANGE_START, RANGE_END)).toHaveLength(1);
    expect(adaptClinicalEventToEvents(outOfRange, RANGE_START, RANGE_END)).toHaveLength(0);
  });

  it('honors schedule_kind="recurring" when frequency metadata is declared', () => {
    const event = makeEvent({
      id: 'forced-recurring',
      status: ClinicalEventStatus.ACTIVE,
      onset_date: '2026-03-01',
      schedule_kind: ScheduleKind.RECURRING,
      event_metadata: { frequency: RecurrenceFrequency.DAILY, interval: 1 },
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    expect(events.length).toBe(31); // March has 31 days
    events.forEach(e => expect(e.kind).toBe('point'));
  });

  it('falls back to status heuristic when schedule_kind="recurring" lacks frequency', () => {
    // No frequency declared — recurring kind cannot expand. Should fall through
    // to the heuristic, which sees ACTIVE + no resolved_date → state.
    const event = makeEvent({
      id: 'recurring-no-freq',
      status: ClinicalEventStatus.ACTIVE,
      onset_date: '2026-03-01',
      schedule_kind: ScheduleKind.RECURRING,
      event_metadata: {},
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('state');
  });

  it('reads recurrence from TOP-LEVEL event_metadata (the shape ClinicalEventForm writes)', () => {
    // Phase 7: the form flattens `recurrence.{frequency,...}` to top-level
    // `event_metadata.{frequency,...}` on submit so the adapter sees it.
    // Verify a form-produced payload produces per-day occurrences.
    const event = makeEvent({
      id: 'form-recurring',
      status: ClinicalEventStatus.ACTIVE,
      onset_date: '2026-03-02', // Monday
      schedule_kind: ScheduleKind.RECURRING,
      event_metadata: {
        // Top-level (post-flatten) — what the form submits.
        frequency: RecurrenceFrequency.WEEKLY,
        interval: 1,
        days_of_week: ['mon', 'wed'],
        time_of_day: '09:00',
      },
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    // March 2026 Mondays (2,9,16,23,30) + Wednesdays (4,11,18,25) = 9.
    expect(events).toHaveLength(9);
    events.forEach(e => expect(e.kind).toBe('point'));
    expect(events[0].time).toBe('09:00');
  });

  it('falls back to status heuristic when schedule_kind is missing (synthetic / partially-loaded row)', () => {
    // Phase 8a: schedule_kind is NOT NULL on the wire, so a missing value here
    // is a runtime safety net for synthetic test rows / partially-loaded data.
    const event = makeEvent({
      id: 'synthetic-legacy',
      status: ClinicalEventStatus.ACTIVE,
      onset_date: '2026-03-01',
      schedule_kind: undefined,
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('state');
  });

  it('still emits explicit occurrences[] alongside declared schedule_kind', () => {
    const event = makeEvent({
      id: 'state-with-occs',
      status: ClinicalEventStatus.ACTIVE,
      onset_date: '2026-03-01',
      schedule_kind: ScheduleKind.STATE,
      occurrences: [{ date: '2026-03-10', intensity: 8 }],
    });
    const events = adaptClinicalEventToEvents(event, RANGE_START, RANGE_END);
    expect(events).toHaveLength(2);
    expect(events.find(e => e.kind === 'state')).toBeDefined();
    expect(events.find(e => e.kind === 'point')).toBeDefined();
  });
});

describe('getActiveConditions / groupActiveConditionsByCategory', () => {
  it('includes state events whose onset is today or earlier', () => {
    const events = [
      makeCalEvent({ id: 'a', kind: 'state', date: parseISO('2026-03-01'), originalData: { id: 'src-a' } }),
    ];
    const out = getActiveConditions(events, NOW);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('a');
  });

  it('includes range events currently within [onset, endDate]', () => {
    const events = [
      makeCalEvent({
        id: 'b',
        kind: 'range',
        date: parseISO('2026-04-10'),
        endDate: parseISO('2026-04-20'),
        originalData: { id: 'src-b' },
      }),
    ];
    expect(getActiveConditions(events, NOW)).toHaveLength(1);
  });

  it('excludes range events that have already ended', () => {
    const events = [
      makeCalEvent({
        kind: 'range',
        date: parseISO('2026-03-01'),
        endDate: parseISO('2026-03-10'),
        originalData: { id: 'r1' },
      }),
    ];
    expect(getActiveConditions(events, NOW)).toHaveLength(0);
  });

  it('excludes state events with future onset', () => {
    const events = [
      makeCalEvent({
        kind: 'state',
        date: parseISO('2026-05-01'),
        originalData: { id: 'fut' },
      }),
    ];
    expect(getActiveConditions(events, NOW)).toHaveLength(0);
  });

  it('excludes point events entirely', () => {
    const events = [
      makeCalEvent({ kind: 'point', date: parseISO('2026-03-01'), originalData: { id: 'p' } }),
    ];
    expect(getActiveConditions(events, NOW)).toHaveLength(0);
  });

  it('excludes events with no kind (treated as point)', () => {
    const events = [makeCalEvent({ date: parseISO('2026-03-01'), originalData: { id: 'p' } })];
    expect(getActiveConditions(events, NOW)).toHaveLength(0);
  });

  it('dedupes by source id — one row per condition even with multiple adapter outputs', () => {
    const events = [
      makeCalEvent({ id: 'state-1', kind: 'state', date: parseISO('2026-03-01'), originalData: { id: 'src-x' } }),
      // An explicit occurrence from the same source on a different day:
      makeCalEvent({ id: 'occ-1', kind: 'point', date: parseISO('2026-04-10'), originalData: { id: 'src-x' } }),
    ];
    const out = getActiveConditions(events, NOW);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('state-1');
  });

  it('sorts by onset ascending', () => {
    const events = [
      makeCalEvent({ id: 'newer', kind: 'state', date: parseISO('2026-04-01'), originalData: { id: 'b' } }),
      makeCalEvent({ id: 'older', kind: 'state', date: parseISO('2026-01-01'), originalData: { id: 'a' } }),
    ];
    const out = getActiveConditions(events, NOW);
    expect(out[0].id).toBe('older');
    expect(out[1].id).toBe('newer');
  });

  // Phase 5: day-cell bars call getActiveConditions with each visible day.
  // Verify per-day semantics explicitly.
  it('returns the state event when queried for a day strictly after onset', () => {
    const events = [
      makeCalEvent({ id: 'state', kind: 'state', date: parseISO('2026-03-01'), originalData: { id: 'a' } }),
    ];
    // NOW is 2026-04-15; ask about an intermediate day.
    const out = getActiveConditions(events, parseISO('2026-03-15'));
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('state');
  });

  it('returns no state event when queried for a day strictly before onset', () => {
    const events = [
      makeCalEvent({ id: 'state', kind: 'state', date: parseISO('2026-03-01'), originalData: { id: 'a' } }),
    ];
    const out = getActiveConditions(events, parseISO('2026-02-15'));
    expect(out).toHaveLength(0);
  });

  it('returns no range event when queried for a day after its resolved_date', () => {
    const events = [
      makeCalEvent({
        id: 'range',
        kind: 'range',
        date: parseISO('2026-03-01'),
        endDate: parseISO('2026-03-10'),
        originalData: { id: 'a' },
      }),
    ];
    // Range closed on Mar 10; asking about Mar 15 should yield nothing.
    expect(getActiveConditions(events, parseISO('2026-03-15'))).toHaveLength(0);
    // But asking about a day inside the range should include it.
    expect(getActiveConditions(events, parseISO('2026-03-05'))).toHaveLength(1);
  });

  it('handles the onset day itself as active (inclusive bound)', () => {
    const events = [
      makeCalEvent({ id: 'state', kind: 'state', date: parseISO('2026-03-01'), originalData: { id: 'a' } }),
    ];
    expect(getActiveConditions(events, parseISO('2026-03-01'))).toHaveLength(1);
  });

  it('handles the resolved_date day itself as still active (inclusive bound)', () => {
    const events = [
      makeCalEvent({
        id: 'range',
        kind: 'range',
        date: parseISO('2026-03-01'),
        endDate: parseISO('2026-03-10'),
        originalData: { id: 'a' },
      }),
    ];
    expect(getActiveConditions(events, parseISO('2026-03-10'))).toHaveLength(1);
  });
});

describe('groupActiveConditionsByCategory', () => {
  it('groups events by category, preserving onset order within group', () => {
    const events = [
      makeCalEvent({ id: '1', kind: 'state', category: 'Chronic', date: parseISO('2026-01-01'), originalData: { id: 'a' } }),
      makeCalEvent({ id: '2', kind: 'state', category: 'Acute', date: parseISO('2026-02-01'), originalData: { id: 'b' } }),
      makeCalEvent({ id: '3', kind: 'state', category: 'Chronic', date: parseISO('2026-03-01'), originalData: { id: 'c' } }),
    ];
    const groups = groupActiveConditionsByCategory(events, 'Other');
    expect(groups).toHaveLength(2);
    const chronic = groups.find(g => g.label === 'Chronic')!;
    expect(chronic.items.map(i => i.id)).toEqual(['1', '3']);
  });

  it('falls back to provided label when category is missing', () => {
    const events = [
      makeCalEvent({ id: '1', kind: 'state', date: parseISO('2026-01-01'), originalData: { id: 'a' } }),
    ];
    const groups = groupActiveConditionsByCategory(events, 'Uncategorized');
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('Uncategorized');
  });
});
