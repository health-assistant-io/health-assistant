/**
 * Tests for the STATE biomarker branch in `useBiomarkers`
 * (plan state-biomarkers-2026-08-05 Step 12).
 *
 * Three regression guards:
 *   1. STATE observations populate `value.state` + `value.stateDisplay`
 *      (not `value.raw`).
 *   2. The legacy `parseFloat(b.value) || 0` data-corruption is fixed —
 *      string state values no longer collapse to 0.
 *   3. STATE observations dedup by state code (not the null `value.raw`).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import type { Observation } from '../../types/observation';
import type { Biomarker } from '../../types/biomarker';

// Mock biomarkerService so the definition-catalog cache doesn't hit the
// network — we only need the observations path for these tests.
vi.mock('../../services/biomarkerService', () => ({
  default: {
    getAllBiomarkers: vi.fn().mockResolvedValue([] as Biomarker[]),
    getUnits: vi.fn().mockResolvedValue([]),
  },
}));

const V3 = 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation';

function makeStateObservation(overrides: Partial<Observation> = {}): Observation {
  return {
    id: 'obs-1',
    tenant_id: 't1',
    status: 'final',
    code: { text: 'SARS-CoV-2 PCR', coding: [{ code: '94500-6', system: 'http://loinc.org' }] },
    subject: { reference: 'Patient/p1' },
    effective_datetime: '2024-01-01T00:00:00Z',
    biomarker_id: 'b1',
    biomarker_slug: 'sars-cov-2-pcr',
    biomarker_aliases: [],
    value_codeable_concept: {
      coding: [{ code: 'POS', system: V3, display: 'Positive' }],
    },
    ...overrides,
  };
}

function makeQuantityObservation(overrides: Partial<Observation> = {}): Observation {
  return {
    id: 'obs-q1',
    tenant_id: 't1',
    status: 'final',
    code: { text: 'Glucose' },
    subject: { reference: 'Patient/p1' },
    effective_datetime: '2024-01-01T00:00:00Z',
    biomarker_id: 'b2',
    biomarker_slug: 'glucose',
    value_quantity: { value: 5.5, unit: 'mmol/L' },
    raw_value: 5.5,
    normalized_value: 5.5,
    ...overrides,
  };
}

describe('useBiomarkers — STATE biomarker branch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('extracts state code + display into value.state (not value.raw)', async () => {
    const { useBiomarkers } = await import('../useBiomarkers');
    const { result } = renderHook(({ observations }) => useBiomarkers({ observations }), {
      initialProps: { observations: [makeStateObservation()] as Observation[] },
    });

    await waitFor(() => {
      expect(result.current.biomarkers).toHaveLength(1);
    });

    const obs = result.current.biomarkers[0];
    expect(obs.valueType).toBe('state');
    expect(obs.value.state).toBe('POS');
    expect(obs.value.stateDisplay).toBe('Positive');
    expect(obs.value.stateSystem).toBe(V3);
    // The numeric slots are null — STATE values are unitless.
    expect(obs.value.raw).toBeNull();
    expect(obs.value.normalized).toBeNull();
  });

  it('leaves the QUANTITY path unchanged (numeric value.raw)', async () => {
    const { useBiomarkers } = await import('../useBiomarkers');
    const { result } = renderHook(({ observations }) => useBiomarkers({ observations }), {
      initialProps: { observations: [makeQuantityObservation()] as Observation[] },
    });

    await waitFor(() => {
      expect(result.current.biomarkers).toHaveLength(1);
    });

    const obs = result.current.biomarkers[0];
    expect(obs.valueType).toBe('quantity');
    expect(obs.value.raw).toBe(5.5);
    expect(obs.value.normalized).toBe(5.5);
    expect(obs.value.state).toBeUndefined();
  });

  it('does not collapse a state display string to 0 (legacy parseFloat corruption regression)', async () => {
    // Drive the legacy document path (doc.entities.biomarkers without
    // known_biomarkers) — pre-fix this did `parseFloat(b.value) || 0` and
    // silently destroyed "Positive" → 0.
    const { useBiomarkers } = await import('../useBiomarkers');
    const doc = {
      id: 'doc-1',
      filename: 'lab.pdf',
      examination_date: '2024-01-01',
      entities: {
        document_category: 'blood_laboratory',
        biomarkers: [
          { name: 'SARS-CoV-2 PCR', value: 'Positive', unit: '', biomarker_id: 'b1' },
        ],
      },
    } as any;

    const { result } = renderHook(({ documents }) => useBiomarkers({ documents }), {
      initialProps: { documents: [doc] as any[] },
    });

    await waitFor(() => {
      expect(result.current.biomarkers).toHaveLength(1);
    });

    const obs = result.current.biomarkers[0];
    expect(obs.valueType).toBe('state');
    expect(obs.value.raw).toBeNull();
    expect(obs.value.stateDisplay).toBe('Positive');
    // Critically: NOT 0.
    expect(obs.value.raw).not.toBe(0);
  });

  it('dedups STATE observations by state code (not the null value.raw)', async () => {
    // Two distinct STATE observations for the same biomarker at the same
    // examination — POS and NEG. They should NOT dedup away each other
    // (different state codes). Pre-fix the dedup key used `value.raw`
    // (null for both) and would collapse them.
    const { useBiomarkers } = await import('../useBiomarkers');
    const observations: Observation[] = [
      makeStateObservation({
        id: 'pos-1',
        value_codeable_concept: { coding: [{ code: 'POS', system: V3, display: 'Positive' }] },
      }),
      makeStateObservation({
        id: 'neg-1',
        value_codeable_concept: { coding: [{ code: 'NEG', system: V3, display: 'Negative' }] },
      }),
    ];

    const { result } = renderHook(({ observations: o }) => useBiomarkers({ observations: o }), {
      initialProps: { observations },
    });

    await waitFor(() => {
      expect(result.current.biomarkers).toHaveLength(2);
    });

    const states = result.current.biomarkers.map((b) => b.value.state).sort();
    expect(states).toEqual(['NEG', 'POS']);
  });
});
