import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../services/documentService', () => ({
  getDocuments: vi.fn(),
}));

import { documentAdapter } from '../documentAdapter';
import { examinationAdapter } from '../examinationAdapter';
import { medicationAdapter } from '../medicationAdapter';
import { observationAdapter } from '../observationAdapter';
import { getDocuments } from '../../../../services/documentService';
import type { InstanceQuery } from '../../../../components/instances/types';

const query = (over: Partial<InstanceQuery> = {}): InstanceQuery => ({
  patientId: 'p1',
  limit: 50,
  offset: 0,
  serverParams: {},
  ...over,
});

describe('adapter row/selection/route projection', () => {
  it('examination projects category, date, and detail route', () => {
    const exam: any = {
      id: 'e1',
      category_concept: { name: 'Blood Test' },
      notes: 'Routine check',
      examination_date: '2026-01-02',
      extraction_status: 'completed',
    };
    const row = examinationAdapter.toRow(exam);
    expect(row.label).toBe('Blood Test');
    expect(row.date).toBe('2026-01-02');
    expect(row.status).toBe('completed');
    expect(examinationAdapter.detailRoute(exam)).toBe('/examinations/e1');
    expect(examinationAdapter.toSelection(exam).label).toBe('Blood Test');
  });

  it('examination subtitle strips HTML to a plain snippet (notes are Quill HTML)', () => {
    const exam: any = {
      id: 'e1',
      category_concept: { name: 'Blood Test' },
      notes: '<p>Routine <strong>checkup</strong> with <em>notes</em></p>',
      examination_date: '2026-01-02',
      extraction_status: 'completed',
    };
    const row = examinationAdapter.toRow(exam);
    // No raw markup leaks into the one-line subtitle.
    expect(row.subtitle).toBe('Routine checkup with notes');
    // Full rich text is preserved on `description` for FormattedText.
    expect(row.description).toBe('<p>Routine <strong>checkup</strong> with <em>notes</em></p>');
  });

  it('medication colors active green / stopped red', () => {
    const active = medicationAdapter.toRow({ id: 'm1', code: { text: 'Aspirin' }, status: 'active' } as any);
    const stopped = medicationAdapter.toRow({ id: 'm2', code: { text: 'Ibuprofen' }, status: 'stopped' } as any);
    expect(active.statusColor).toBe('#16a34a');
    expect(stopped.statusColor).toBe('#dc2626');
    expect(medicationAdapter.detailRoute({ id: 'm1' } as any)).toBe(
      '/medications/details/m1',
    );
  });

  it('observation builds value subtitle from value_quantity', () => {
    const obs: any = {
      id: 'o1',
      code: { text: 'Glucose' },
      value_quantity: { value: 5.4, unit: 'mmol/L' },
      effective_datetime: '2026-03-01T00:00:00Z',
      interpretation: 'High',
    };
    const row = observationAdapter.toRow(obs);
    expect(row.subtitle).toBe('5.4 mmol/L');
    expect(row.statusColor).toBe('#dc2626');
    expect(observationAdapter.detailRoute(obs)).toBeNull();
  });

  it('observation coerces a CodeableConcept interpretation {text} to a string', () => {
    // The single-fetch endpoint emits interpretation as a FHIR CodeableConcept,
    // not a plain string — the adapter must collapse it so the card renders a
    // string and React never receives an object child.
    const obs: any = {
      id: 'o2',
      code: { text: 'Glucose' },
      interpretation: { text: 'High', coding: [{ display: 'High' }] },
    };
    const row = observationAdapter.toRow(obs);
    expect(row.status).toBe('High');
    expect(row.statusColor).toBe('#dc2626');
  });
});

describe('documentAdapter.fetch', () => {
  beforeEach(() => vi.clearAllMocks());

  it('scopes to the requested patient client-side', async () => {
    (getDocuments as any).mockResolvedValue([
      { id: 'd1', filename: 'a.pdf', status: 'uploaded', patient_id: 'p1' },
      { id: 'd2', filename: 'b.pdf', status: 'uploaded', patient_id: 'p2' },
    ]);
    const res = await documentAdapter.fetch(query({ patientId: 'p1' }));
    expect(res.items).toHaveLength(1);
    expect(res.items[0].id).toBe('d1');
  });

  it('applies the q substring filter after patient scoping', async () => {
    (getDocuments as any).mockResolvedValue([
      { id: 'd1', filename: 'lab.pdf', status: 'uploaded', patient_id: 'p1' },
      { id: 'd2', filename: 'x-ray.pdf', status: 'uploaded', patient_id: 'p1' },
    ]);
    const res = await documentAdapter.fetch(query({ patientId: 'p1', q: 'lab' }));
    expect(res.items).toHaveLength(1);
    expect(res.items[0].filename).toBe('lab.pdf');
    expect(res.total).toBe(1);
  });
});
