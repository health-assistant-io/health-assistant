/**
 * RefRangesField — renders a biomarker's stratified `reference_ranges` array as
 * a compact table (Sex / Age / Range / Notes).
 *
 * Each range row carries `{sex, age_min, age_max, low, high, text,
 * applies_to, unit_id}`. Per Phase 0, `unit_id` is a bare UUID (no symbol
 * expansion in the list payload), so the human-readable range comes from the
 * `text` field when present, falling back to `low`–`high`. A null/empty array
 * renders a muted dash.
 */
import React from 'react';
import { RangeBar } from '../../../ui/RangeBar';

export interface BiomarkerReferenceRange {
  sex?: string | null;
  age_min?: number | null;
  age_max?: number | null;
  low?: number | null;
  high?: number | null;
  text?: string | null;
  applies_to?: string | null;
  unit_id?: string | null;
}

interface RefRangesFieldProps {
  value: unknown;
}

function orDash(v: unknown): string {
  return v === null || v === undefined || v === '' ? '—' : String(v);
}

export const RefRangesField: React.FC<RefRangesFieldProps> = ({ value }) => {
  if (!Array.isArray(value) || value.length === 0) {
    return <span className="text-gray-400">—</span>;
  }
  const ranges = value as BiomarkerReferenceRange[];

  // Pick the first range with a numeric low+high for the summary number-line.
  const summary = ranges.find(
    (rr) =>
      (rr.low === null || rr.low === undefined || Number.isFinite(rr.low)) &&
      (rr.high === null || rr.high === undefined || Number.isFinite(rr.high)) &&
      Number.isFinite(rr.low as number) &&
      Number.isFinite(rr.high as number) &&
      (rr.high as number) > (rr.low as number),
  );

  return (
    <div className="space-y-2">
      {summary && Number.isFinite(summary.low) && Number.isFinite(summary.high) && (
        <RangeBar low={summary.low as number} high={summary.high as number} />
      )}
      <div className="overflow-x-auto -mx-1">
        <table className="min-w-full text-xs border-separate border-spacing-0">
        <thead>
          <tr className="text-left text-gray-400">
            <th className="font-medium py-1 pr-3">Sex</th>
            <th className="font-medium py-1 pr-3">Age</th>
            <th className="font-medium py-1 pr-3">Range</th>
            <th className="font-medium py-1">Notes</th>
          </tr>
        </thead>
        <tbody>
          {ranges.map((rr, i) => {
            let range: string;
            if (rr.text && rr.text.trim()) {
              range = rr.text.trim();
            } else if (rr.low != null || rr.high != null) {
              range = `${orDash(rr.low)} – ${orDash(rr.high)}`;
            } else {
              range = '—';
            }
            const age =
              (rr.age_min === null || rr.age_min === undefined) &&
              (rr.age_max === null || rr.age_max === undefined)
                ? 'Any'
                : `${orDash(rr.age_min)} – ${orDash(rr.age_max)}`;
            return (
              <tr key={i} className="text-gray-700 dark:text-gray-200">
                <td className="py-1 pr-3 align-top">{rr.sex ? orDash(rr.sex) : 'Any'}</td>
                <td className="py-1 pr-3 align-top">{age}</td>
                <td className="py-1 pr-3 align-top font-mono">{range}</td>
                <td className="py-1 align-top text-gray-500 dark:text-gray-400">
                  {orDash(rr.applies_to)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </div>
  );
};

export default RefRangesField;
