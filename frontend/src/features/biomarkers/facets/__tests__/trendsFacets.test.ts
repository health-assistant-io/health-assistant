import { describe, it, expect } from 'vitest';
import type { FilterValue } from '../../../../components/ui/filters';
import type { BiomarkerObservation } from '../../../../types/biomarker';
import {
  biomarkerStatusFacet,
  biomarkerSourceTypeFacet,
  biomarkerSubcategoryFacet,
  biomarkerUnitObsFacet,
  biomarkerSourceFacet,
  biomarkerMappedFacet,
  trendsBiomarkerFacets,
} from '../trendsFacets';

const multi = (values: string[]): FilterValue => ({ kind: 'multi', values });
const toggle = (on: boolean): FilterValue => ({ kind: 'toggle', on });

function obs(overrides: Partial<BiomarkerObservation>): BiomarkerObservation {
  return {
    id: 'x',
    displayName: 'X',
    slug: 'x',
    method: null,
    value: { raw: 100, normalized: null },
    unit: { rawSymbol: 'mg/dL' },
    referenceRange: { min: 50, max: 150, displayText: '50-150' },
    relativeScore: null,
    interpretation: 'Normal',
    source: { documentId: 'd1', filename: 'f.pdf', date: '2026-01-01' },
    definitionId: 'def1',
    info: null,
    ...overrides,
  };
}

const observations: BiomarkerObservation[] = [
  obs({ id: 'normal', displayName: 'NormalOne', value: { raw: 100, normalized: null } }),
  obs({ id: 'high', displayName: 'HighOne', value: { raw: 200, normalized: null } }),
  obs({ id: 'low', displayName: 'LowOne', value: { raw: 10, normalized: null } }),
  obs({ id: 'telemetry', displayName: 'TelemetryOne', isTelemetry: true, unit: { rawSymbol: 'bpm' } }),
  obs({ id: 'lab2', displayName: 'LabTwo', source: { documentId: 'd2', filename: 'g.pdf', date: '2026-01-02', labName: 'BioLab' } }),
  obs({ id: 'unmapped', displayName: 'UnmappedOne', isUnmapped: true, definitionId: null }),
  obs({ id: 'exam', displayName: 'ExamOne', source: { documentId: 'd3', filename: 'h.pdf', date: '2026-01-03', examinationId: 'ex1' } }),
  obs({ id: 'labpanel', displayName: 'LabPanel', _rawJson: { techCategory: 'laboratory' } }),
];

describe('biomarkerStatusFacet', () => {
  it('derives Normal/High/Low options via getFinalStatus with counts', () => {
    const opts = biomarkerStatusFacet.getOptions!(observations);
    const byVal = Object.fromEntries(opts.map((o) => [o.value, o.count]));
    expect(byVal.Normal).toBeGreaterThan(0);
    expect(byVal.High).toBe(1);
    expect(byVal.Low).toBe(1);
  });

  it('keeps items matching a selected status', () => {
    expect(biomarkerStatusFacet.predicate!(observations[1], multi(['High']))).toBe(true);
    expect(biomarkerStatusFacet.predicate!(observations[0], multi(['High']))).toBe(false);
  });

  it('passes everything when no status is selected', () => {
    expect(biomarkerStatusFacet.predicate!(observations[0], multi([]))).toBe(true);
  });
});

describe('biomarkerSourceTypeFacet', () => {
  it('classifies telemetry as system, exam-derived as examination, else technical', () => {
    expect(biomarkerSourceTypeFacet.predicate!(observations[3], multi(['system']))).toBe(true);
    expect(biomarkerSourceTypeFacet.predicate!(observations[6], multi(['examination']))).toBe(true);
    expect(biomarkerSourceTypeFacet.predicate!(observations[0], multi(['technical']))).toBe(true);
    // telemetry row is NOT also tagged examination even if it had an exam id
    expect(biomarkerSourceTypeFacet.predicate!(observations[3], multi(['examination']))).toBe(false);
  });

  it('labels the options as System / Technical / Examination', () => {
    const labels = biomarkerSourceTypeFacet.getOptions!(observations).map((o) => o.label);
    expect(labels).toEqual(expect.arrayContaining(['System', 'Technical', 'Examination']));
  });
});

describe('biomarkerSubcategoryFacet', () => {
  it('derives options from _rawJson.techCategory / document_category', () => {
    const opts = biomarkerSubcategoryFacet.getOptions!([observations[7]]);
    expect(opts.map((o) => o.value)).toEqual(['laboratory']);
  });

  it('keeps rows whose subcategory is selected', () => {
    expect(biomarkerSubcategoryFacet.predicate!(observations[7], multi(['laboratory']))).toBe(true);
    expect(biomarkerSubcategoryFacet.predicate!(observations[0], multi(['laboratory']))).toBe(false);
  });
});

describe('biomarkerUnitObsFacet', () => {
  it('derives options from normalizedSymbol falling back to rawSymbol', () => {
    const opts = biomarkerUnitObsFacet.getOptions!(observations);
    const values = opts.map((o) => o.value);
    expect(values).toContain('mg/dL');
    expect(values).toContain('bpm');
  });

  it('matches on the resolved symbol', () => {
    expect(biomarkerUnitObsFacet.predicate!(observations[0], multi(['mg/dL']))).toBe(true);
    expect(biomarkerUnitObsFacet.predicate!(observations[3], multi(['mg/dL']))).toBe(false);
    expect(biomarkerUnitObsFacet.predicate!(observations[3], multi(['bpm']))).toBe(true);
  });
});

describe('biomarkerSourceFacet', () => {
  it('derives options only from observations that have a labName', () => {
    const opts = biomarkerSourceFacet.getOptions!(observations);
    expect(opts.map((o) => o.value)).toEqual(['BioLab']);
  });

  it('excludes observations without a labName when a filter is active', () => {
    expect(biomarkerSourceFacet.predicate!(observations[4], multi(['BioLab']))).toBe(true);
    expect(biomarkerSourceFacet.predicate!(observations[0], multi(['BioLab']))).toBe(false);
  });
});

describe('biomarkerMappedFacet', () => {
  it('hides unmapped observations when the toggle is on', () => {
    expect(biomarkerMappedFacet.predicate!(observations[5], toggle(true))).toBe(false);
    expect(biomarkerMappedFacet.predicate!(observations[0], toggle(true))).toBe(true);
  });

  it('shows everything when the toggle is off', () => {
    expect(biomarkerMappedFacet.predicate!(observations[5], toggle(false))).toBe(true);
  });
});

describe('trendsBiomarkerFacets', () => {
  it('exposes all six facets in display order', () => {
    expect(trendsBiomarkerFacets.map((f) => f.id)).toEqual([
      'status',
      'source_type',
      'subcategory',
      'unit',
      'source',
      'mapped',
    ]);
  });

  it('every facet is client-mode', () => {
    for (const f of trendsBiomarkerFacets) expect(f.mode).toBe('client');
  });

  it('every facet has a predicate', () => {
    for (const f of trendsBiomarkerFacets) expect(typeof f.predicate).toBe('function');
  });
});
