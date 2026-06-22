import { describe, it, expect } from 'vitest';
import {
  formatBiomarkerValue,
  getPrecisionForValue,
  formatUnit,
  getFinalStatus,
  isAbnormal,
  getStatusColorClass,
  type BiomarkerPrecisionProfile,
} from '../biomarkerUtils';

const PROFILE: BiomarkerPrecisionProfile = {
  default: 0,
  below_30: 1,
  below_10: 1,
  below_3: 2,
  below_1: 3,
};

describe('formatBiomarkerValue', () => {
  it('returns "--" for null/undefined/empty', () => {
    expect(formatBiomarkerValue(null, PROFILE)).toBe('--');
    expect(formatBiomarkerValue(undefined, PROFILE)).toBe('--');
    expect(formatBiomarkerValue('', PROFILE)).toBe('--');
  });

  it('returns the string representation for non-numeric values', () => {
    expect(formatBiomarkerValue('N/A', PROFILE)).toBe('N/A');
    expect(formatBiomarkerValue('trace', PROFILE)).toBe('trace');
  });

  it('applies magnitude-aware precision from profile', () => {
    expect(formatBiomarkerValue(120, PROFILE)).toBe('120');         // |v| >= 30 → 0 decimals
    expect(formatBiomarkerValue(15.5, PROFILE)).toBe('15.5');       // 10..30  → 1 decimal
    expect(formatBiomarkerValue(7.3, PROFILE)).toBe('7.3');         // 3..10   → 1 decimal
    expect(formatBiomarkerValue(2.5, PROFILE)).toBe('2.50');        // 1..3    → 2 decimals
    expect(formatBiomarkerValue(0.42, PROFILE)).toBe('0.420');      // < 1     → 3 decimals
  });

  it('accepts a plain number as uniform precision', () => {
    expect(formatBiomarkerValue(120, 2)).toBe('120.00');
    expect(formatBiomarkerValue(0.5, 0)).toBe('1');
  });

  it('defaults to precision 0 when no profile given', () => {
    expect(formatBiomarkerValue(7.345)).toBe('7');
  });

  it('handles string-encoded numbers', () => {
    expect(formatBiomarkerValue('15.5', PROFILE)).toBe('15.5');
    expect(formatBiomarkerValue('0.42', PROFILE)).toBe('0.420');
  });

  it('handles negative values with correct magnitude bucket', () => {
    expect(formatBiomarkerValue(-0.5, PROFILE)).toBe('-0.500');
    expect(formatBiomarkerValue(-42, PROFILE)).toBe('-42');
  });

  it('clamps invalid precision values to 0..6', () => {
    const bad: any = { default: -5, below_30: 99, below_10: NaN, below_3: 2, below_1: 3 };
    expect(formatBiomarkerValue(50, bad)).toBe('50');             // default -5 → clamped to 0
    expect(formatBiomarkerValue(20, bad)).toBe('20.000000');      // below_30 99 → clamped to 6
    expect(formatBiomarkerValue(5, bad)).toBe('5');               // below_10 NaN → clamped to 0
  });
});

describe('getPrecisionForValue', () => {
  it('picks the correct tier by magnitude', () => {
    expect(getPrecisionForValue(100, PROFILE)).toBe(0);
    expect(getPrecisionForValue(30, PROFILE)).toBe(0);
    expect(getPrecisionForValue(29.9, PROFILE)).toBe(1);
    expect(getPrecisionForValue(10, PROFILE)).toBe(1);
    expect(getPrecisionForValue(9.9, PROFILE)).toBe(1);
    expect(getPrecisionForValue(3, PROFILE)).toBe(1);   // 3 is NOT < 3, falls to below_10
    expect(getPrecisionForValue(2.9, PROFILE)).toBe(2);
    expect(getPrecisionForValue(1, PROFILE)).toBe(2);   // 1 is NOT < 1, falls to below_3
    expect(getPrecisionForValue(0.9, PROFILE)).toBe(3);
  });

  it('returns a plain number as-is (clamped)', () => {
    expect(getPrecisionForValue(50, 3)).toBe(3);
    expect(getPrecisionForValue(50, -1)).toBe(0);
    expect(getPrecisionForValue(50, 10)).toBe(6);
  });
});

describe('formatUnit', () => {
  it('converts caret notation to superscripts', () => {
    expect(formatUnit('10^9/L')).toBe('10⁹/L');
    expect(formatUnit('10^3/uL')).toBe('10³/uL');
    expect(formatUnit('cm^2')).toBe('cm²');
  });

  it('returns empty string for falsy input', () => {
    expect(formatUnit('')).toBe('');
    expect(formatUnit(null as any)).toBe('');
  });

  it('passes through units without carets unchanged', () => {
    expect(formatUnit('mg/dL')).toBe('mg/dL');
    expect(formatUnit('mmol/L')).toBe('mmol/L');
  });
});

describe('getFinalStatus', () => {
  const make = (raw: any, min?: number, max?: number, interpretation?: string) => ({
    value: { raw },
    interpretation,
    referenceRange: { min, max },
  });

  it('returns High when value exceeds max', () => {
    expect(getFinalStatus(make(200, 50, 150) as any)).toBe('High');
  });

  it('returns Low when value below min', () => {
    expect(getFinalStatus(make(20, 50, 150) as any)).toBe('Low');
  });

  it('returns Normal when within range', () => {
    expect(getFinalStatus(make(100, 50, 150) as any)).toBe('Normal');
  });

  it('falls back to interpretation when no range', () => {
    expect(getFinalStatus(make(100, undefined, undefined, 'High') as any)).toBe('High');
    expect(getFinalStatus(make(100, undefined, undefined, 'low') as any)).toBe('Low');
  });

  it('returns Normal as default', () => {
    expect(getFinalStatus(make(100) as any)).toBe('Normal');
  });
});

describe('isAbnormal', () => {
  it('identifies abnormal statuses', () => {
    expect(isAbnormal('High')).toBe(true);
    expect(isAbnormal('LOW')).toBe(true);
    expect(isAbnormal('abnormal')).toBe(true);
  });

  it('considers normal/stable as not abnormal', () => {
    expect(isAbnormal('Normal')).toBe(false);
    expect(isAbnormal('N')).toBe(false);
    expect(isAbnormal('within range')).toBe(false);
  });
});

describe('getStatusColorClass', () => {
  it('returns red for high/abnormal', () => {
    expect(getStatusColorClass('High')).toContain('red');
    expect(getStatusColorClass('abnormal')).toContain('red');
  });

  it('returns blue for low', () => {
    expect(getStatusColorClass('Low')).toContain('blue');
  });

  it('returns green for normal', () => {
    expect(getStatusColorClass('Normal')).toContain('green');
  });

  it('returns yellow for warning', () => {
    expect(getStatusColorClass('warning')).toContain('yellow');
  });
});
