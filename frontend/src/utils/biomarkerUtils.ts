import { BiomarkerObservation } from '../types/biomarker';

/**
 * Calculates the final status of a biomarker observation based on its value and reference range,
 * falling back to the extracted interpretation if range is missing.
 */
export const getFinalStatus = (b: BiomarkerObservation): string => {
  if (!b) return 'Normal';
  
  const val = typeof b.value?.raw === 'number' ? b.value.raw : parseFloat(b.value?.raw as any);
  const min = b.referenceRange?.min;
  const max = b.referenceRange?.max;
  
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
  const s = (b.interpretation || '').toLowerCase().trim();
  if (s.includes('high') || s === 'h') return 'High';
  if (s.includes('low') || s === 'l') return 'Low';
  if (s.includes('warning')) return 'Warning';
  if (s.includes('abnormal')) return 'Abnormal';
  if (s.includes('normal') || s === 'n') return 'Normal';
  
  return 'Normal';
};

/**
 * Checks if a status string represents an abnormal value.
 */
export const isAbnormal = (status: string): boolean => {
  if (!status) return false;
  const s = status.toLowerCase().trim();
  if (['normal', 'n', 'stable', 'within range'].includes(s) || s.includes('normal')) return false;
  return ['high', 'low', 'h', 'l', 'abnormal', 'warning'].includes(s) || 
         s.includes('high') || s.includes('low') || s.includes('abnormal') || s.includes('warn');
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
export const formatUnit = (unit: string): string => {
  if (!unit) return '';
  
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
