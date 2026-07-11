import { describe, it, expect } from 'vitest';
import { domainRouteForType } from '../domainRoute';

describe('domainRouteForType', () => {
  it('returns the biomarker detail route by id', () => {
    expect(domainRouteForType('biomarker', 'abc-123')).toBe(
      '/biomarkers/details/abc-123',
    );
  });

  it('returns the medication detail route by id', () => {
    expect(domainRouteForType('medication', 'med-1')).toBe(
      '/medications/details/med-1',
    );
  });

  it('prefers slug for anatomy when provided', () => {
    expect(domainRouteForType('anatomy', 'id-1', 'thyroid')).toBe(
      '/anatomy/thyroid',
    );
  });

  it('falls back to id for anatomy when no slug is provided', () => {
    expect(domainRouteForType('anatomy', 'id-1')).toBe('/anatomy/id-1');
  });

  it('returns null for catalog types with no dedicated domain page', () => {
    expect(domainRouteForType('allergy', 'a1')).toBeNull();
    expect(domainRouteForType('vaccine', 'v1')).toBeNull();
    expect(domainRouteForType('concept', 'c1')).toBeNull();
  });

  it('returns null for unknown types', () => {
    expect(domainRouteForType('mystery', 'x')).toBeNull();
    expect(domainRouteForType('', 'x')).toBeNull();
  });

  it('is case-insensitive on the catalog type', () => {
    expect(domainRouteForType('Biomarker', 'b1')).toBe(
      '/biomarkers/details/b1',
    );
    expect(domainRouteForType('MEDICATION', 'm1')).toBe(
      '/medications/details/m1',
    );
  });
});
