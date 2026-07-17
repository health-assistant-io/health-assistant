import { describe, it, expect } from 'vitest';
import { getInstanceFacets, getObservationFacets, getExaminationFacets } from '../index';
import { computeStatus, getObservationStatus } from '../../../../utils/biomarkerUtils';
import type { Observation } from '../../../../types/observation';

describe('instance facet registry', () => {
  it('dispatches facets per type', () => {
    expect(getInstanceFacets('observation').map((f) => f.id)).toEqual([
      'status',
      'unit',
      'mapped',
    ]);
    expect(getInstanceFacets('examination').map((f) => f.id)).toEqual([
      'category',
      'status',
    ]);
    expect(getInstanceFacets('medication').map((f) => f.id)).toEqual(['status']);
    expect(getInstanceFacets('event').map((f) => f.id)).toEqual([
      'category',
      'status',
    ]);
    // unknown type → no facets (graceful)
    expect(getInstanceFacets('nope')).toEqual([]);
  });

  it('category facet accepts injected options (listing pages) and derives otherwise', () => {
    const withCtx = getExaminationFacets({ categoryOptions: [{ value: 'Lab', label: 'Lab' }] });
    expect((withCtx[0] as any).options).toEqual([{ value: 'Lab', label: 'Lab' }]);
    expect((getExaminationFacets()[0] as any).getOptions).toBeTypeOf('function');
  });

  it('observation status facet recomputes via getObservationStatus (range-based)', () => {
    const facet = getObservationFacets().find((f) => f.id === 'status')!;
    const normal = { value_quantity: { value: 5, unit: 'mmol/L' }, lab_reference_range: { min: 3, max: 7 } } as Observation;
    const high = { value_quantity: { value: 9, unit: 'mmol/L' }, lab_reference_range: { min: 3, max: 7 } } as Observation;
    expect(facet.predicate!(normal, { kind: 'multi', values: ['Normal'] })).toBe(true);
    expect(facet.predicate!(high, { kind: 'multi', values: ['Normal'] })).toBe(false);
    expect(facet.predicate!(high, { kind: 'multi', values: ['High'] })).toBe(true);
  });
});

describe('computeStatus / getObservationStatus (single status algorithm)', () => {
  it('range-based result wins over the stored interpretation', () => {
    // value inside range → Normal even if stored interpretation says High
    expect(computeStatus(5, 3, 7, 'High')).toBe('Normal');
    expect(computeStatus(9, 3, 7, 'Normal')).toBe('High');
  });

  it('falls back to interpretation when no usable range', () => {
    expect(computeStatus(5, null, null, 'High')).toBe('High');
    expect(computeStatus(undefined, 3, 7, 'Low')).toBe('Low');
  });

  it('getObservationStatus reads raw Observation fields + coerces CodeableConcept interpretation', () => {
    const inRange = {
      normalized_value: 5,
      biomarker_reference_range_min: 3,
      biomarker_reference_range_max: 7,
    } as Observation;
    expect(getObservationStatus(inRange)).toBe('Normal');
    const over = { normalized_value: 9, biomarker_reference_range_max: 7 } as Observation;
    expect(getObservationStatus(over)).toBe('High');
    const fallback = { interpretation: { text: 'Low' } } as unknown as Observation;
    expect(getObservationStatus(fallback)).toBe('Low');
  });
});
