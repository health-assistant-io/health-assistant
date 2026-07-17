import { BiomarkerObservation } from '../types/biomarker';
import type { Observation } from '../types/observation';
import { codeableText } from './textFormat';

/**
 * The single biomarker-status algorithm. Range-based check first (most
 * accurate), then interpretation-string fallback. Both {@link getFinalStatus}
 * (enriched {@link BiomarkerObservation}) and {@link getObservationStatus}
 * (raw {@link Observation}) delegate here so the status is computed one way
 * everywhere — browse modal, trends, and listings agree.
 */
export function computeStatus(
  rawVal: unknown,
  min: number | null | undefined,
  max: number | null | undefined,
  interpretation: string | null | undefined,
): string {
  const val = typeof rawVal === 'number' ? rawVal : parseFloat(rawVal as any);

  // 1. Range based check (Most accurate)
  if (val !== null && val !== undefined && !isNaN(val)) {
    if (min !== null && min !== undefined && val < min) return 'Low';
    if (max !== null && max !== undefined && val > max) return 'High';

    // If we have at least one valid range boundary and we're between them, it's explicitly Normal
    if ((min !== null && min !== undefined) || (max !== null && max !== undefined)) {
      return 'Normal';
    }
  }

  // 2. Fallback to existing interpretation
  const s = (codeableText(interpretation) ?? '').toLowerCase().trim();
  if (s.includes('high') || s === 'h') return 'High';
  if (s.includes('low') || s === 'l') return 'Low';
  if (s.includes('warning')) return 'Warning';
  if (s.includes('abnormal')) return 'Abnormal';
  if (s.includes('normal') || s === 'n') return 'Normal';

  return 'Normal';
}

/**
 * Calculates the final status of a biomarker observation based on its value and reference range,
 * falling back to the extracted interpretation if range is missing.
 */
export const getFinalStatus = (b: BiomarkerObservation): string => {
  if (!b) return 'Normal';
  return computeStatus(b.value?.raw, b.referenceRange?.min, b.referenceRange?.max, b.interpretation);
};

/**
 * Status for a raw {@link Observation} (the ORM-shape from /observations/*),
 * using the same algorithm as {@link getFinalStatus}. Pulls value + range from
 * the raw fields the backend serializes (`normalized_value`/`raw_value`,
 * `lab_reference_range`, `biomarker_reference_range_*`) and coerces the FHIR
 * CodeableConcept `interpretation` to a string.
 */
export const getObservationStatus = (o: Observation | null | undefined): string => {
  if (!o) return 'Normal';
  const val = o.normalized_value ?? o.raw_value ?? o.value_quantity?.value;
  let min: number | null | undefined;
  let max: number | null | undefined;
  const range = o.lab_reference_range;
  if (range && typeof range === 'object') {
    min = (range as any).min ?? (range as any).low?.value;
    max = (range as any).max ?? (range as any).high?.value;
  }
  if (min == null) min = o.biomarker_reference_range_min;
  if (max == null) max = o.biomarker_reference_range_max;
  return computeStatus(val, min, max, codeableText(o.interpretation));
};


/**
 * Checks if a status string represents an abnormal value.
 */
export const isAbnormal = (status: string): boolean => {
  if (!status) return false;
  const s = status.toLowerCase().trim();
  if (['high', 'low', 'h', 'l', 'abnormal', 'warning'].includes(s) ||
      s.includes('abnormal') || s.includes('warn')) return true;
  if (['normal', 'n', 'stable', 'within range'].includes(s) || s.includes('normal')) return false;
  return s.includes('high') || s.includes('low');
};

/**
 * Returns the tailwind CSS classes for a status badge based on the status string.
 */
export const getStatusColorClass = (status: string): string => {
  const s = status.toLowerCase().trim();
  if (s.includes('high') || s === 'h' || s === 'abnormal') return 'bg-red-50 text-red-700 border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-900/30';
  if (s.includes('low') || s === 'l') return 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-900/30';
  if (s.includes('warning')) return 'bg-yellow-50 text-yellow-700 border-yellow-200 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-900/30';
  return 'bg-green-50 text-green-700 border-green-200 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/30';
};

/**
 * Formats medical units to properly display superscripts (e.g., 10^3 -> 10³).
 */
export const formatUnit = (unit: string): string => {  if (!unit) return '';
  
  const superscriptMap: Record<string, string> = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾',
    'n': 'ⁿ'
  };

  return unit.replace(/\^([0-9+\-=()n]+)/g, (_, p1) => {
    return p1.split('').map((char: string) => superscriptMap[char] || char).join('');
  });
};

/**
 * Formats a biomarker numeric value to the configured decimal precision.
 * Non-numeric / qualitative values are returned unchanged.
 *
 * Accepts either a single precision number (uniform) or a magnitude-aware
 * profile that picks precision based on the absolute value's magnitude.
 */
export interface BiomarkerPrecisionProfile {
  /** precision for |value| >= 30 */
  default: number;
  /** precision for 10 <= |value| < 30 */
  below_30: number;
  /** precision for 3 <= |value| < 10 */
  below_10: number;
  /** precision for 1 <= |value| < 3 */
  below_3: number;
  /** precision for |value| < 1 */
  below_1: number;
}

export const getPrecisionForValue = (
  value: number,
  profile: BiomarkerPrecisionProfile | number
): number => {
  if (typeof profile === 'number') {
    return _clampPrecision(profile);
  }
  const abs = Math.abs(value);
  if (abs < 1) return _clampPrecision(profile.below_1);
  if (abs < 3) return _clampPrecision(profile.below_3);
  if (abs < 10) return _clampPrecision(profile.below_10);
  if (abs < 30) return _clampPrecision(profile.below_30);
  return _clampPrecision(profile.default);
};

const _clampPrecision = (p: any): number => {
  const n = typeof p === 'number' && isFinite(p) ? p : 0;
  return Math.max(0, Math.min(6, Math.round(n)));
};

export const formatBiomarkerValue = (
  value: any,
  precision: BiomarkerPrecisionProfile | number = 0
): string => {
  if (value === null || value === undefined || value === '') return '--';
  const num = typeof value === 'number' ? value : parseFloat(value);
  if (!isFinite(num)) return String(value);
  const p = getPrecisionForValue(num, precision);
  return num.toFixed(p);
};
