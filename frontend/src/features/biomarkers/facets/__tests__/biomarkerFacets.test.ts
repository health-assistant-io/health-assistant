import { describe, it, expect } from 'vitest';
import type { FilterValue } from '../../../../components/ui/filters';
import type { Biomarker } from '../../../../types/biomarker';
import {
  biomarkerCategoryFacet,
  biomarkerTelemetryFacet,
  biomarkerCodingSystemFacet,
  biomarkerUnitFacet,
  catalogBiomarkerFacets,
} from '../biomarkerFacets';

const multi = (values: string[]): FilterValue => ({ kind: 'multi', values });
const toggle = (on: boolean): FilterValue => ({ kind: 'toggle', on });

const biomarkers: Biomarker[] = [
  { id: '1', slug: 'ldl', name: 'LDL', category: 'Lipids', coding_system: 'loinc', preferred_unit_symbol: 'mg/dL', is_telemetry: false, aliases: [] },
  { id: '2', slug: 'hdl', name: 'HDL', category: 'Lipids', coding_system: 'loinc', preferred_unit_symbol: 'mg/dL', is_telemetry: false, aliases: [] },
  { id: '3', slug: 'glucose', name: 'Glucose', category: 'Glucose', coding_system: 'snomed', preferred_unit_symbol: 'mmol/L', is_telemetry: true, aliases: [] },
  { id: '4', slug: 'heart-rate', name: 'Heart Rate', coding_system: 'custom', preferred_unit_symbol: 'bpm', is_telemetry: true, aliases: [] },
  { id: '5', slug: 'orphan', name: 'Orphan', aliases: [] },
];

describe('biomarkerCategoryFacet', () => {
  it('derives counted options excluding undefined categories', () => {
    const opts = biomarkerCategoryFacet.getOptions!(biomarkers);
    expect(opts.map((o) => o.value)).toEqual(['Lipids', 'Glucose']);
    expect(opts.find((o) => o.value === 'Lipids')?.count).toBe(2);
  });

  it('includes items whose category is in the selection', () => {
    expect(biomarkerCategoryFacet.predicate!(biomarkers[0], multi(['Lipids']))).toBe(true);
    expect(biomarkerCategoryFacet.predicate!(biomarkers[2], multi(['Lipids']))).toBe(false);
  });

  it('excludes items with no category when a filter is active', () => {
    expect(biomarkerCategoryFacet.predicate!(biomarkers[4], multi(['Lipids']))).toBe(false);
  });

  it('passes everything when the selection is empty', () => {
    expect(biomarkerCategoryFacet.predicate!(biomarkers[4], multi([]))).toBe(true);
  });
});

describe('biomarkerTelemetryFacet', () => {
  it('passes everything when toggle is off', () => {
    expect(biomarkerTelemetryFacet.predicate!(biomarkers[0], toggle(false))).toBe(true);
  });

  it('keeps only telemetry biomarkers when toggle is on', () => {
    expect(biomarkerTelemetryFacet.predicate!(biomarkers[3], toggle(true))).toBe(true);
    expect(biomarkerTelemetryFacet.predicate!(biomarkers[0], toggle(true))).toBe(false);
  });
});

describe('biomarkerCodingSystemFacet', () => {
  it('uppercases option labels', () => {
    const opts = biomarkerCodingSystemFacet.getOptions!(biomarkers);
    // loinc (count 2) first; then snomed & custom (count 1) sorted by label asc.
    expect(opts.map((o) => o.label)).toEqual(['LOINC', 'CUSTOM', 'SNOMED']);
  });

  it('matches by the raw value, not the label', () => {
    expect(biomarkerCodingSystemFacet.predicate!(biomarkers[0], multi(['loinc']))).toBe(true);
    expect(biomarkerCodingSystemFacet.predicate!(biomarkers[2], multi(['loinc']))).toBe(false);
  });
});

describe('biomarkerUnitFacet', () => {
  it('derives options from preferred_unit_symbol', () => {
    const opts = biomarkerUnitFacet.getOptions!(biomarkers);
    expect(opts.map((o) => o.value).sort()).toEqual(['bpm', 'mg/dL', 'mmol/L']);
  });

  it('filters by unit symbol', () => {
    expect(biomarkerUnitFacet.predicate!(biomarkers[0], multi(['mg/dL']))).toBe(true);
    expect(biomarkerUnitFacet.predicate!(biomarkers[2], multi(['mg/dL']))).toBe(false);
  });
});

describe('catalogBiomarkerFacets', () => {
  it('exposes all four facets in display order', () => {
    expect(catalogBiomarkerFacets.map((f) => f.id)).toEqual([
      'category',
      'is_telemetry',
      'coding_system',
      'unit',
    ]);
  });

  it('every facet is client-mode (no backend coupling in Phase 2)', () => {
    for (const f of catalogBiomarkerFacets) expect(f.mode).toBe('client');
  });

  it('every facet has a predicate', () => {
    for (const f of catalogBiomarkerFacets) expect(typeof f.predicate).toBe('function');
  });
});
