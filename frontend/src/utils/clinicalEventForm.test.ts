import { describe, it, expect } from 'vitest';
import { ClinicalEventStatus, ScheduleKind } from '../services/clinicalEventService';
import {
  getScheduleKind,
  shouldShowEndDate,
  kindHintKey,
  computeDurationDays,
  defaultStatusForKind,
} from './clinicalEventForm';

describe('getScheduleKind', () => {
  it('returns the declared schedule_kind when set', () => {
    expect(getScheduleKind({ schedule_kind: ScheduleKind.STATE } as any)).toBe(ScheduleKind.STATE);
    expect(getScheduleKind({ schedule_kind: ScheduleKind.RANGE } as any)).toBe(ScheduleKind.RANGE);
    expect(getScheduleKind({ schedule_kind: ScheduleKind.POINT } as any)).toBe(ScheduleKind.POINT);
    expect(getScheduleKind({ schedule_kind: ScheduleKind.RECURRING } as any)).toBe(ScheduleKind.RECURRING);
  });

  it('falls back to STATE when schedule_kind is missing or the type is unloaded', () => {
    // Phase 8a: schedule_kind is required on the wire (NOT NULL), so a missing
    // value here is a runtime safety net for partial loads / synthetic rows,
    // not a legacy wire-format case.
    expect(getScheduleKind({} as any)).toBe(ScheduleKind.STATE);
    expect(getScheduleKind(null)).toBe(ScheduleKind.STATE);
    expect(getScheduleKind(undefined)).toBe(ScheduleKind.STATE);
  });
});

describe('shouldShowEndDate', () => {
  it('range kind: always shows End Date regardless of status', () => {
    expect(shouldShowEndDate(ScheduleKind.RANGE, ClinicalEventStatus.ACTIVE)).toBe(true);
    expect(shouldShowEndDate(ScheduleKind.RANGE, ClinicalEventStatus.RESOLVED)).toBe(true);
    expect(shouldShowEndDate(ScheduleKind.RANGE, ClinicalEventStatus.ON_HOLD)).toBe(true);
    expect(shouldShowEndDate(ScheduleKind.RANGE, ClinicalEventStatus.UNKNOWN)).toBe(true);
  });

  it('point kind: never shows End Date', () => {
    expect(shouldShowEndDate(ScheduleKind.POINT, ClinicalEventStatus.ACTIVE)).toBe(false);
    expect(shouldShowEndDate(ScheduleKind.POINT, ClinicalEventStatus.RESOLVED)).toBe(false);
  });

  it('state kind: shows End Date only when RESOLVED', () => {
    expect(shouldShowEndDate(ScheduleKind.STATE, ClinicalEventStatus.ACTIVE)).toBe(false);
    expect(shouldShowEndDate(ScheduleKind.STATE, ClinicalEventStatus.RESOLVED)).toBe(true);
    expect(shouldShowEndDate(ScheduleKind.STATE, ClinicalEventStatus.ON_HOLD)).toBe(false);
    expect(shouldShowEndDate(ScheduleKind.STATE, ClinicalEventStatus.UNKNOWN)).toBe(false);
  });

  it('recurring kind: shows End Date only when RESOLVED (schedule ended)', () => {
    expect(shouldShowEndDate(ScheduleKind.RECURRING, ClinicalEventStatus.ACTIVE)).toBe(false);
    expect(shouldShowEndDate(ScheduleKind.RECURRING, ClinicalEventStatus.RESOLVED)).toBe(true);
  });
});

describe('kindHintKey', () => {
  it('uses the resolved-state hint for state+RESOLVED', () => {
    expect(kindHintKey(ScheduleKind.STATE, ClinicalEventStatus.RESOLVED)).toBe('events.kind_hint_state_resolved');
  });

  it('uses the resolved-recurring hint for recurring+RESOLVED', () => {
    expect(kindHintKey(ScheduleKind.RECURRING, ClinicalEventStatus.RESOLVED)).toBe('events.kind_hint_recurring_resolved');
  });

  it('uses the default kind hint otherwise', () => {
    expect(kindHintKey(ScheduleKind.STATE, ClinicalEventStatus.ACTIVE)).toBe('events.kind_hint_state');
    expect(kindHintKey(ScheduleKind.RANGE, ClinicalEventStatus.RESOLVED)).toBe('events.kind_hint_range');
    expect(kindHintKey(ScheduleKind.POINT, ClinicalEventStatus.ACTIVE)).toBe('events.kind_hint_point');
    expect(kindHintKey(ScheduleKind.RECURRING, ClinicalEventStatus.ACTIVE)).toBe('events.kind_hint_recurring');
  });
});

describe('computeDurationDays', () => {
  it('returns the inclusive day count between two YYYY-MM-DD strings', () => {
    expect(computeDurationDays('2026-03-01', '2026-03-10')).toBe(9);
    expect(computeDurationDays('2026-03-01', '2026-03-01')).toBe(0);
    expect(computeDurationDays('2026-01-01', '2026-04-01')).toBe(90);
  });

  it('returns null when either date is missing', () => {
    expect(computeDurationDays('', '2026-03-10')).toBeNull();
    expect(computeDurationDays('2026-03-01', '')).toBeNull();
    expect(computeDurationDays('', '')).toBeNull();
  });

  it('returns null for malformed dates', () => {
    expect(computeDurationDays('not-a-date', '2026-03-10')).toBeNull();
    expect(computeDurationDays('2026-03-01', 'garbage')).toBeNull();
  });

  it('returns null when end is before start (ill-formed range)', () => {
    expect(computeDurationDays('2026-03-10', '2026-03-01')).toBeNull();
  });
});

describe('defaultStatusForKind', () => {
  it('returns ACTIVE for every kind (point may diverge later)', () => {
    expect(defaultStatusForKind(ScheduleKind.STATE)).toBe(ClinicalEventStatus.ACTIVE);
    expect(defaultStatusForKind(ScheduleKind.RANGE)).toBe(ClinicalEventStatus.ACTIVE);
    expect(defaultStatusForKind(ScheduleKind.POINT)).toBe(ClinicalEventStatus.ACTIVE);
    expect(defaultStatusForKind(ScheduleKind.RECURRING)).toBe(ClinicalEventStatus.ACTIVE);
  });
});

describe('ScheduleKind wire-format values (Phase 8a)', () => {
  it('lowercase values match the backend Python enum .value (wire format)', () => {
    expect(ScheduleKind.STATE).toBe('state');
    expect(ScheduleKind.RANGE).toBe('range');
    expect(ScheduleKind.RECURRING).toBe('recurring');
    expect(ScheduleKind.POINT).toBe('point');
  });
});
