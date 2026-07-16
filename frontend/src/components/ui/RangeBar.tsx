/**
 * RangeBar — a compact horizontal number-line visualizing a normal reference
 * interval (low–high) as a shaded band within a padded domain.
 *
 * Used by the biomarker reference-ranges view to show the normal band at a
 * glance; reusable for any low/high interval (biomarker-detail, analytics).
 * Values are clamped to the domain; a non-numeric or inverted range renders
 * nothing (caller falls back to text). An optional `value` marker (e.g. a
 * current observation) is drawn as a dot — not used by the catalog Info tab
 * (a definition has no current value) but available to other consumers.
 */
import React from 'react';

interface RangeBarProps {
  low: number;
  high: number;
  /** Domain bounds; default to one interval-width of padding on each side
   *  (clamped to ≥ 0 when both bounds are non-negative). */
  min?: number;
  max?: number;
  value?: number | null;
  unit?: string;
  className?: string;
}

function pct(v: number, domainMin: number, domainMax: number): number {
  if (domainMax === domainMin) return 0;
  return ((v - domainMin) / (domainMax - domainMin)) * 100;
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '');
}

export const RangeBar: React.FC<RangeBarProps> = ({
  low,
  high,
  min,
  max,
  value,
  unit,
  className = '',
}) => {
  if (
    !Number.isFinite(low) ||
    !Number.isFinite(high) ||
    high <= low
  ) {
    return null;
  }

  const span = high - low;
  let domainMin = min ?? low - span;
  let domainMax = max ?? high + span;
  // Clamp the domain floor to ≥ 0 for non-negative ranges (typical for labs).
  if (domainMin < 0 && low >= 0) {
    const shift = -domainMin;
    domainMin = 0;
    domainMax += shift;
  }
  if (domainMax <= domainMin) domainMax = domainMin + span;

  const left = Math.max(0, Math.min(100, pct(low, domainMin, domainMax)));
  const right = Math.max(0, Math.min(100, pct(high, domainMin, domainMax)));
  const valuePct =
    value !== null && value !== undefined && Number.isFinite(value)
      ? Math.max(0, Math.min(100, pct(value, domainMin, domainMax)))
      : null;

  return (
    <div className={`w-full ${className}`}>
      <div className="relative h-4 w-full bg-gray-100 dark:bg-gray-700 rounded-full">
        <div
          className="absolute h-full bg-emerald-300 dark:bg-emerald-700/60 rounded-full"
          style={{ left: `${left}%`, width: `${Math.max(0, right - left)}%` }}
          title={`Normal: ${fmt(low)} – ${fmt(high)}${unit ? ' ' + unit : ''}`}
        />
        {valuePct !== null && (
          <div
            className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full bg-blue-600 border-2 border-white dark:border-gray-800 shadow"
            style={{ left: `${valuePct}%` }}
            title={`Value: ${fmt(value as number)}${unit ? ' ' + unit : ''}`}
          />
        )}
      </div>
      <div className="flex justify-between text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">
        <span>{fmt(domainMin)}</span>
        <span className="font-medium text-gray-500 dark:text-gray-400">
          {fmt(low)} – {fmt(high)}{unit ? ` ${unit}` : ''}
        </span>
        <span>{fmt(domainMax)}</span>
      </div>
    </div>
  );
};

export default RangeBar;
