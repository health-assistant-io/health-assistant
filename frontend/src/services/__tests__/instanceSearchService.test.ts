import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../../api/axios';
import { searchInstances } from '../instanceSearchService';

vi.mock('../../api/axios', () => ({ default: { get: vi.fn() } }));

describe('instanceSearchService', () => {
  beforeEach(() => vi.clearAllMocks());

  it('builds the query string with q, patient_id, limit, types', async () => {
    (api.get as any).mockResolvedValue({
      data: { results: [{ type: 'medication', id: 'm1', label: 'Aspirin' }] },
    });
    const hits = await searchInstances({
      q: 'asp',
      patientId: 'p1',
      limit: 5,
      types: ['medication', 'allergy'],
    });
    expect(hits).toEqual([{ type: 'medication', id: 'm1', label: 'Aspirin' }]);
    const args = (api.get as any).mock.calls[0];
    expect(args[0]).toBe('/instances/search');
    expect(args[1].params).toEqual({
      q: 'asp',
      patient_id: 'p1',
      limit: '5',
      types: 'medication,allergy',
    });
  });

  it('omits optional params when not provided', async () => {
    (api.get as any).mockResolvedValue({ data: { results: [] } });
    await searchInstances({ q: 'xyz' });
    const params = (api.get as any).mock.calls[0][1].params;
    expect(params).toEqual({ q: 'xyz' });
    expect(params.patient_id).toBeUndefined();
    expect(params.types).toBeUndefined();
  });

  it('registers all seven adapters on import', async () => {
    // Importing the adapters barrel registers them in the instance registry.
    await import('../../features/instances/adapters');
    const { hasAdapter } = await import('../../components/instances/instanceRegistry');
    for (const t of [
      'examination',
      'medication',
      'observation',
      'document',
      'event',
      'allergy',
      'vaccine',
    ]) {
      expect(hasAdapter(t as any)).toBe(true);
    }
  });
});
