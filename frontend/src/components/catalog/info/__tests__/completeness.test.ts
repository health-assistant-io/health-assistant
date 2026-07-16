import { describe, it, expect } from 'vitest';

import { getFieldCompleteness } from '../completeness';
import type { CatalogItem } from '../../../../types/catalog';

const item = (overrides: Record<string, unknown> = {}): CatalogItem =>
  ({ name: 'X', ...overrides } as CatalogItem);

describe('getFieldCompleteness', () => {
  it('is complete when item is null', () => {
    expect(getFieldCompleteness('biomarker', null)).toEqual({ complete: true, missing: [] });
  });

  it('flags a biomarker missing both code and unit', () => {
    const r = getFieldCompleteness('biomarker', item({}));
    expect(r.complete).toBe(false);
    expect(r.missing.sort()).toEqual(['code', 'unit']);
  });

  it('is satisfied when the biomarker has a code and a unit symbol', () => {
    const r = getFieldCompleteness(
      'biomarker',
      item({ code: '2345-7', preferred_unit_symbol: 'mg/dL' }),
    );
    expect(r.complete).toBe(true);
  });

  it('accepts preferred_unit_id as a unit substitute', () => {
    const r = getFieldCompleteness(
      'biomarker',
      item({ code: '2345-7', preferred_unit_id: 'u-1' }),
    );
    expect(r.complete).toBe(true);
  });

  it('flags an allergy missing category', () => {
    const r = getFieldCompleteness('allergy', item({}));
    expect(r.missing).toEqual(['category']);
    expect(getFieldCompleteness('allergy', item({ category: 'FOOD' })).complete).toBe(true);
  });

  it('flags a vaccine missing code', () => {
    expect(getFieldCompleteness('vaccine', item({})).missing).toEqual(['code']);
    expect(getFieldCompleteness('vaccine', item({ code: '03' })).complete).toBe(true);
  });

  it('treats medication / anatomy / concept as always complete', () => {
    for (const t of ['medication', 'anatomy', 'concept'] as const) {
      expect(getFieldCompleteness(t, item({})).complete).toBe(true);
    }
  });

  it('treats an unknown type as complete', () => {
    expect(getFieldCompleteness(undefined, item({})).complete).toBe(true);
  });
});
