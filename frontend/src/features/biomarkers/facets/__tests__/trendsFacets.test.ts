import { describe, it, expect } from 'vitest';
import type { FilterValue } from '../../../../components/ui/filters';
import type { BiomarkerObservation } from '../../../../types/biomarker';
import {
  biomarkerStatusFacet,
  biomarkerTelemetryObsFacet,
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

describe('biomarkerTelemetryObsFacet', () => {
  it('keeps only telemetry observations when on', () => {
    expect(biomarkerTelemetryObsFacet.predicate!(observations[3], toggle(true))).toBe(true);
    expect(biomarkerTelemetryObsFacet.predicate!(observations[0], toggle(true))).toBe(false);
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
  it('exposes all five facets in display order', () => {
    expect(trendsBiomarkerFacets.map((f) => f.id)).toEqual([
      'status',
      'telemetry',
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
