/**
 * DoseScheduleField — renders a vaccine's `dose_schedule` object
 * (`{doses: number|null, intervals: string[]}`) as a dose count + interval
 * chips. A null/empty schedule renders a muted dash.
 */
import React from 'react';
import { ChipList } from '../../../ui/ChipList';

export interface DoseSchedule {
  doses?: number | null;
  intervals?: string[] | null;
}

interface DoseScheduleFieldProps {
  value: unknown;
}

export const DoseScheduleField: React.FC<DoseScheduleFieldProps> = ({ value }) => {
  if (!value || typeof value !== 'object') {
    return <span className="text-gray-400">—</span>;
  }
  const schedule = value as DoseSchedule;
  const doses = schedule.doses;
  const intervals = Array.isArray(schedule.intervals) ? schedule.intervals : [];
  const hasDoses = doses !== null && doses !== undefined;
  const hasIntervals = intervals.length > 0;

  if (!hasDoses && !hasIntervals) {
    return <span className="text-gray-400">—</span>;
  }

  return (
    <div className="flex flex-col gap-1.5">
      {hasDoses && (
        <span className="text-sm">
          <span className="font-semibold">{doses}</span>{' '}
          <span className="text-gray-500 dark:text-gray-400">
            {doses === 1 ? 'dose' : 'doses'}
          </span>
        </span>
      )}
      {hasIntervals && (
        <div>
          <span className="block text-[10px] uppercase tracking-wider text-gray-400 mb-1">
            Intervals
          </span>
          <ChipList items={intervals} variant="info" />
        </div>
      )}
    </div>
  );
};

export default DoseScheduleField;
