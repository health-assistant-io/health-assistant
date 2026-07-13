import { describe, it, expect } from 'vitest';
import { getFacetsForType } from '../catalogFacetRegistry';

describe('catalogFacetRegistry', () => {
  it('returns the biomarker facets for "biomarker"', () => {
    const facets = getFacetsForType('biomarker');
    expect(facets.map((f) => f.id)).toEqual([
      'category',
      'is_telemetry',
      'coding_system',
      'unit',
    ]);
  });

  it('returns allergy facets for "allergy"', () => {
    expect(getFacetsForType('allergy').map((f) => f.id)).toEqual(['category', 'is_custom']);
  });

  it('returns vaccine facets for "vaccine"', () => {
    expect(getFacetsForType('vaccine').map((f) => f.id)).toEqual(['coding_system']);
  });

  it('returns medication facets for "medication"', () => {
    expect(getFacetsForType('medication').map((f) => f.id)).toEqual(['is_custom']);
  });

  it('returns concept facets for "concept"', () => {
    expect(getFacetsForType('concept').map((f) => f.id)).toEqual(['status']);
  });

  it('returns an empty array for types without facets', () => {
    expect(getFacetsForType('anatomy')).toEqual([]);
    expect(getFacetsForType('')).toEqual([]);
    expect(getFacetsForType('nonexistent')).toEqual([]);
  });
});
