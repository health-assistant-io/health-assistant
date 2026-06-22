import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useBiomarkerChange } from '../useBiomarkerChange';

describe('useBiomarkerChange', () => {
  it('returns null for fewer than 2 data points', () => {
    const { rerender, result } = renderHook(({ data }: { data: any[] }) => useBiomarkerChange(data), {
      initialProps: { data: [] as Array<{ value: number }> },
    });
    expect(result.current).toBeNull();

    rerender({ data: [{ value: 5 }] });
    expect(result.current).toBeNull();
  });

  it('returns null when previous value is 0 (division by zero guard)', () => {
    const { result } = renderHook(({ data }) => useBiomarkerChange(data), {
      initialProps: { data: [{ value: 0 }, { value: 5 }] },
    });
    expect(result.current).toBeNull();
  });

  it('detects an upward trend', () => {
    const { result } = renderHook(({ data }) => useBiomarkerChange(data), {
      initialProps: { data: [{ value: 100 }, { value: 120 }] },
    });
    expect(result.current).not.toBeNull();
    expect(result.current!.isUp).toBe(true);
    expect(result.current!.isNeutral).toBe(false);
    expect(result.current!.percent).toBe('20.0');
    expect(result.current!.color).toBe('text-emerald-500');
  });

  it('detects a downward trend', () => {
    const { result } = renderHook(({ data }) => useBiomarkerChange(data), {
      initialProps: { data: [{ value: 100 }, { value: 80 }] },
    });
    expect(result.current!.isUp).toBe(false);
    expect(result.current!.percent).toBe('20.0');
    expect(result.current!.color).toBe('text-red-500');
  });

  it('detects a neutral change (no difference)', () => {
    const { result } = renderHook(({ data }) => useBiomarkerChange(data), {
      initialProps: { data: [{ value: 50 }, { value: 50 }] },
    });
    expect(result.current!.isNeutral).toBe(true);
    expect(result.current!.color).toBe('text-gray-400');
  });

  it('returns absolute percentage (always positive)', () => {
    const { result } = renderHook(({ data }) => useBiomarkerChange(data), {
      initialProps: { data: [{ value: 100 }, { value: 50 }] },
    });
    expect(result.current!.percent).toBe('50.0');
    expect(parseFloat(result.current!.percent)).toBeGreaterThanOrEqual(0);
  });

  it('formats percentage to 1 decimal place', () => {
    const { result } = renderHook(({ data }) => useBiomarkerChange(data), {
      initialProps: { data: [{ value: 3 }, { value: 4 }] },
    });
    // (4-3)/3 * 100 = 33.333... → "33.3"
    expect(result.current!.percent).toBe('33.3');
  });

  it('returns null for undefined input', () => {
    const { result } = renderHook(({ data }) => useBiomarkerChange(data), {
      initialProps: { data: undefined },
    });
    expect(result.current).toBeNull();
  });
});
