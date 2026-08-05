import { useMemo } from 'react';

export interface BiomarkerChangeInfo {
  /** absolute percentage formatted to 1 decimal place, e.g. "15.3" */
  percent: string;
  isUp: boolean;
  isNeutral: boolean;
  color: string;
}

/**
 * Computes the percentage change between the last two points of a biomarker
 * trend series. Returns null when there are fewer than 2 points, the
 * previous value is zero (would divide by zero), or any value is non-numeric
 * (STATE biomarkers — categorical values have no meaningful percentage
 * change; the cards should hide the delta badge for those series).
 *
 * Used by BiomarkerCard, TrendsCard, and any card that shows a ↑/↓ delta.
 */
export function useBiomarkerChange(
  data: Array<{ value: number | string | null | undefined }> | undefined | null
): BiomarkerChangeInfo | null {
  return useMemo(() => {
    if (!data || data.length < 2) return null;
    const latestRaw = data[data.length - 1].value;
    const prevRaw = data[data.length - 2].value;
    // STATE / non-numeric values: short-circuit (no meaningful % change).
    if (typeof latestRaw !== 'number' || typeof prevRaw !== 'number') return null;
    if (!isFinite(latestRaw) || !isFinite(prevRaw)) return null;
    const latest = latestRaw;
    const prev = prevRaw;
    if (prev === 0) return null;
    const diff = latest - prev;
    const percent = (diff / Math.abs(prev)) * 100;
    return {
      percent: Math.abs(percent).toFixed(1),
      isUp: diff > 0,
      isNeutral: diff === 0,
      color: diff > 0 ? 'text-emerald-500' : diff < 0 ? 'text-red-500' : 'text-gray-400',
    };
  }, [data]);
}
