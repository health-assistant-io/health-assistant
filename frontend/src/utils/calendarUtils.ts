import { format, parseISO, startOfDay, addDays, isSameDay } from 'date-fns';
import { MedicationRecord } from '../services/medicationService';
import { AllergyIntolerance } from '../services/allergyService';
import { ClinicalEvent as ClinicalEventModel, ClinicalEventStatus, ScheduleKind, RecurrenceFrequency } from '../services/clinicalEventService';
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
        kind: 'point',
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
            kind: 'point',
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
    kind: 'point',
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
    kind: 'point',
    originalData: allergy
  };
};

/**
 * Adapts a ClinicalEventModel into one or more CalendarEvents.
 *
 * Resolution order (the first match wins):
 *
 * 1. **`event.schedule_kind`** (Phase 4) — the explicit, admin-declared hint
 *    resolved by the backend from the type blueprint. Authoritative since
 *    Phase 8a (NOT NULL on the wire). Values:
 *    - `ScheduleKind.STATE`     — emit one event on onset (kind=`'state'`).
 *                                 Never expanded.
 *    - `ScheduleKind.RANGE`     — emit one event on onset, `endDate` from
 *                                 `resolved_date` if present (kind=`'range'`).
 *    - `ScheduleKind.POINT`     — emit one event on onset (kind=`'point'`).
 *    - `ScheduleKind.RECURRING` — expand per `event_metadata.frequency`
 *                                 (kind=`'point'`); if no frequency is
 *                                 declared, fall through to heuristic.
 *
 * 2. **Status-based heuristic** (Phase 1 behavior, retained as a runtime
 *    safety net for synthetic rows / partially-loaded data missing
 *    `schedule_kind`):
 *    - `status=ACTIVE` + no `resolved_date` + no recurrence metadata → state
 *    - `resolved_date` set + no recurrence → range
 *    - explicit `event_metadata.frequency` → recurring expansion
 *    - otherwise → point
 *
 * Explicit `occurrences[]` always emit as `kind='point'` events in addition.
 */
export const adaptClinicalEventToEvents = (
  event: ClinicalEventModel,
  rangeStart: Date,
  rangeEnd: Date
): CalendarEvent[] => {
  const calendarEvents: CalendarEvent[] = [];
  const meta = event.event_metadata || {};
  const category = event.type_details?.category_concept?.name;
  const declaredKind = event.schedule_kind;

  // 1. Handle explicit occurrences — always point events.
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
          category,
          kind: 'point',
          originalData: event
        });
      }
    });
  }

  // 2. Authoritative declared schedule_kind (Phase 4) — takes precedence.
  //    Phase 8a: `declaredKind` is now guaranteed non-null on rows from the
  //    API (the backend resolves + persists it NOT NULL). The `undefined`
  //    check is retained as a runtime safety net for synthetic test rows /
  //    partially-loaded data.
  if (declaredKind === ScheduleKind.RECURRING) {
    // Recurring only expands if frequency metadata is actually declared;
    // otherwise fall through to the heuristic below.
    if (event.onset_date && typeof meta.frequency === 'string' && meta.frequency) {
      const recEvents = expandRecurrence(event, rangeStart, rangeEnd);
      for (const rec of recEvents) {
        const exists = calendarEvents.some(e => isSameDay(e.date, rec.date));
        if (!exists) calendarEvents.push(rec);
      }
      return calendarEvents;
    }
  } else if (declaredKind === ScheduleKind.STATE || declaredKind === ScheduleKind.RANGE || declaredKind === ScheduleKind.POINT) {
    if (event.onset_date) {
      const onsetDate = parseISO(event.onset_date);
      if (declaredKind === ScheduleKind.STATE) {
        calendarEvents.push(buildSingleEvent(event, 'state', onsetDate, undefined, category));
      } else if (declaredKind === ScheduleKind.RANGE) {
        const resolved = event.resolved_date ? parseISO(event.resolved_date) : undefined;
        calendarEvents.push(buildSingleEvent(event, 'range', onsetDate, resolved, category));
      } else {
        // point — filter by visible range (genuine point-in-time event).
        if (onsetDate >= rangeStart && onsetDate <= rangeEnd) {
          calendarEvents.push(buildSingleEvent(event, 'point', onsetDate, undefined, category));
        }
      }
    }
    return calendarEvents;
  }

  // 3. Legacy / fallback — status-based heuristic.
  //
  //    Missing metadata no longer implies 'daily'. Recurrence expansion only
  //    fires when event_metadata.frequency is explicitly declared.
  if (event.onset_date && typeof meta.frequency === 'string' && meta.frequency) {
    const recEvents = expandRecurrence(event, rangeStart, rangeEnd);
    for (const rec of recEvents) {
      const exists = calendarEvents.some(e => isSameDay(e.date, rec.date));
      if (!exists) calendarEvents.push(rec);
    }
    return calendarEvents;
  }

  if (!event.onset_date) return calendarEvents;

  const onsetDate = parseISO(event.onset_date);

  if (event.status === ClinicalEventStatus.ACTIVE && !event.resolved_date) {
    // Ongoing state — emit one event on onset; no endDate. NOT filtered by
    // the visible range so consumers (e.g. "Currently active") can surface
    // ongoing conditions that started before the window. The calendar day-grid
    // naturally ignores events whose date falls outside the visible month.
    calendarEvents.push(buildSingleEvent(event, 'state', onsetDate, undefined, category));
  } else if (event.resolved_date) {
    // Bounded range — onset → resolved. Emitted unconditionally so consumers
    // can decide based on overlap with their visible window.
    const resolvedDate = parseISO(event.resolved_date);
    calendarEvents.push(buildSingleEvent(event, 'range', onsetDate, resolvedDate, category));
  } else {
    // Point event (e.g. RESOLVED/ON_HOLD/UNKNOWN without resolved_date).
    // Filter by the visible range — these are genuine point-in-time events.
    if (onsetDate < rangeStart || onsetDate > rangeEnd) return calendarEvents;
    calendarEvents.push(buildSingleEvent(event, 'point', onsetDate, undefined, category));
  }

  return calendarEvents;
};

/**
 * Internal helper — constructs one of the state/range/point single-onset events
 * with the common fields filled in. Avoids repetition between the declared-kind
 * branch and the heuristic branch of `adaptClinicalEventToEvents`.
 */
function buildSingleEvent(
  event: ClinicalEventModel,
  kind: 'state' | 'range' | 'point',
  onsetDate: Date,
  resolvedDate: Date | undefined,
  category: string | undefined
): CalendarEvent {
  const idSuffix = kind === 'state' ? 'state'
    : kind === 'range' ? 'range'
    : 'onset';
  const subtitle = kind === 'point'
    ? `Started: ${event.type_details?.name || ''}`
    : (event.description || event.type_details?.name);

  return {
    id: `${event.id}-${idSuffix}`,
    type: 'clinical-event',
    title: event.title,
    subtitle,
    date: onsetDate,
    endDate: resolvedDate,
    status: event.status,
    category,
    kind,
    originalData: event
  };
}

/**
 * Per-day recurrence expansion — only invoked when the event explicitly
 * declares `event_metadata.frequency`. Emits `kind='point'` events.
 */
function expandRecurrence(
  event: ClinicalEventModel,
  rangeStart: Date,
  rangeEnd: Date
): CalendarEvent[] {
  const out: CalendarEvent[] = [];
  const onsetDate = parseISO(event.onset_date!);
  const resolvedDate = event.resolved_date ? parseISO(event.resolved_date) : null;

  const intervalStart = onsetDate > rangeStart ? onsetDate : rangeStart;
  const intervalEnd = (resolvedDate && resolvedDate < rangeEnd)
    ? resolvedDate
    : rangeEnd;
  if (intervalStart > intervalEnd) return out;

  const meta = event.event_metadata || {};
  const frequency = meta.frequency as string;
  const interval = meta.interval || 1;
  const daysOfWeek: string[] = meta.days_of_week || [];
  const category = event.type_details?.category_concept?.name;

  for (let day = startOfDay(intervalStart); day <= intervalEnd; day = addDays(day, 1)) {
    let shouldInclude = false;

    if (frequency === RecurrenceFrequency.DAILY) {
      const diffDays = Math.floor((day.getTime() - startOfDay(onsetDate).getTime()) / (1000 * 60 * 60 * 24));
      shouldInclude = diffDays % interval === 0;
    } else if (frequency === RecurrenceFrequency.WEEKLY) {
      const dayName = format(day, 'eee').toLowerCase();
      if (daysOfWeek.length > 0) {
        shouldInclude = daysOfWeek.includes(dayName);
      } else {
        const diffWeeks = Math.floor((day.getTime() - startOfDay(onsetDate).getTime()) / (1000 * 60 * 60 * 24 * 7));
        shouldInclude = (diffWeeks % interval === 0) && (day.getDay() === onsetDate.getDay());
      }
    } else if (frequency === RecurrenceFrequency.MONTHLY) {
      shouldInclude = day.getDate() === onsetDate.getDate();
    }

    if (shouldInclude) {
      out.push({
        id: `${event.id}-rec-${format(day, 'yyyyMMdd')}`,
        type: 'clinical-event',
        title: event.title,
        subtitle: event.description || event.type_details?.name,
        date: day,
        time: meta.time_of_day || '12:00',
        status: event.status,
        category,
        kind: 'point',
        originalData: event
      });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Active-conditions helpers — pure functions used by the calendar strip and
// the ScheduleSummary. Both surface "what is currently ongoing" without the
// per-day bloat that motivated the kind-aware adapter rewrite.
// ---------------------------------------------------------------------------

/**
 * Returns events that are "currently active" as of `now`:
 *  - `kind === 'state'` (ongoing, no resolved date) — onset <= now
 *  - `kind === 'range'` (bounded episode) — onset <= now <= endDate
 *
 * Deduped by source event id so each underlying condition appears at most
 * once regardless of how many calendar ranges it produced.
 *
 * Future-onset state/range events are NOT included — those surface under
 * "upcoming" instead.
 */
export function getActiveConditions(
  events: CalendarEvent[],
  now: Date = new Date()
): CalendarEvent[] {
  const today = startOfDay(now).getTime();
  const bySource = new Map<string, CalendarEvent>();

  for (const e of events) {
    if (e.kind !== 'state' && e.kind !== 'range') continue;
    if (!(e.date instanceof Date)) continue;

    const onset = e.date.getTime();
    if (onset > today) continue; // future onset → not "currently active"

    const end = e.endDate?.getTime();
    if (end !== undefined && end < today) continue; // already resolved

    const sourceId =
      (e.originalData && (e.originalData as any).id as string) ?? e.id;
    if (!bySource.has(sourceId)) bySource.set(sourceId, e);
  }

  return Array.from(bySource.values()).sort(
    (a, b) => a.date.getTime() - b.date.getTime()
  );
}

/**
 * Groups active conditions by their `category` field (the
 * `type_details.category_concept.name` projected by the adapter). Events
 * without a category fall under `fallbackLabel`. Stable insertion order
 * within each group (sorted by onset ascending, inherited from
 * `getActiveConditions`).
 */
export function groupActiveConditionsByCategory(
  events: CalendarEvent[],
  fallbackLabel: string
): Array<{ label: string; items: CalendarEvent[] }> {
  const groups = new Map<string, CalendarEvent[]>();
  for (const e of events) {
    const label = e.category || fallbackLabel;
    const arr = groups.get(label);
    if (arr) arr.push(e);
    else groups.set(label, [e]);
  }
  return Array.from(groups.entries()).map(([label, items]) => ({ label, items }));
}
