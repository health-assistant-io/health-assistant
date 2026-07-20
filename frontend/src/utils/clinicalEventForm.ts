/**
 * Kind-aware UI helpers for the Record New Clinical Event form. Extracted as
 * pure functions so they can be unit-tested without mounting the component.
 *
 * These helpers encode the form's policy for how `schedule_kind` and event
 * `status` translate into which date fields are visible and what copy is
 * shown. They do NOT touch the data model — that's `adaptClinicalEventToEvents`
 * in `calendarUtils.ts`.
 */
import { ClinicalEventStatus, ScheduleKind, ClinicalEventType } from '../services/clinicalEventService';

/**
 * Resolve the effective `schedule_kind` for a type. Phase 8a tightened the
 * type so `schedule_kind` is required on every loaded row, but this helper
 * still accepts a possibly-null/unloaded type (e.g. while the type catalog
 * is being fetched) and falls back to `STATE` (the safest default — never
 * expands per-day). The fallback is a runtime safety net, not a legacy wire
 * case.
 */
export function getScheduleKind(type?: ClinicalEventType | null): ScheduleKind {
  return type?.schedule_kind ?? ScheduleKind.STATE;
}

/**
 * Whether the End Date (resolved_date) field should be visible for a given
 * kind + status combination:
 *  - range     → always (the episode is bounded by definition)
 *  - state     → only when RESOLVED (the user is recording when it ended)
 *  - recurring → only when RESOLVED (the schedule has ended)
 *  - point     → never (instantaneous event, no end)
 */
export function shouldShowEndDate(kind: ScheduleKind, status: ClinicalEventStatus): boolean {
  if (kind === ScheduleKind.RANGE) return true;
  if (kind === ScheduleKind.POINT) return false;
  // state + recurring
  return status === ClinicalEventStatus.RESOLVED;
}

/** i18n key for the kind hint, contextual to status for state/recurring. */
export function kindHintKey(kind: ScheduleKind, status: ClinicalEventStatus): string {
  if (kind === ScheduleKind.STATE && status === ClinicalEventStatus.RESOLVED) return 'events.kind_hint_state_resolved';
  if (kind === ScheduleKind.RECURRING && status === ClinicalEventStatus.RESOLVED) return 'events.kind_hint_recurring_resolved';
  return `events.kind_hint_${kind}`;
}

/** Days between two YYYY-MM-DD strings. Returns null if either is empty/invalid
 *  or if end < start (treats the range as ill-formed rather than negative). */
export function computeDurationDays(start: string, end: string): number | null {
  if (!start || !end) return null;
  const s = new Date(start);
  const e = new Date(end);
  if (isNaN(s.getTime()) || isNaN(e.getTime())) return null;
  const diff = Math.round((e.getTime() - s.getTime()) / (1000 * 60 * 60 * 24));
  return diff >= 0 ? diff : null;
}

/**
 * Sensible status default per kind. Point events often record something that
 * already happened (a visit, an incident) — but the form still defaults to
 * ACTIVE so the user can decide. State/range/recurring are inherently
 * "ongoing" so ACTIVE is correct.
 */
export function defaultStatusForKind(_kind: ScheduleKind): ClinicalEventStatus {
  return ClinicalEventStatus.ACTIVE;
}
