import { beforeEach, describe, expect, it, vi } from 'vitest';
import api from '../../api/axios';
import {
  addPatientMedication,
  getMedication,
  getMedicationUsage,
  getPatientMedications,
  updatePatientMedication,
  type MedicationRecord,
} from '../medicationService';

vi.mock('../../api/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

function record(overrides: Partial<MedicationRecord> = {}): MedicationRecord {
  return {
    id: 'med-1',
    patient_id: 'p-1',
    tenant_id: 't-1',
    status: 'active',
    code: { text: 'Amoxicillin' },
    start_date: '2026-11-08',
    created_at: '2026-11-08T00:00:00Z',
    ...overrides,
  };
}

describe('medicationService status boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('maps the API uppercase enum to the client vocabulary on reads', async () => {
    (api.get as any).mockResolvedValue({
      data: [
        record({ id: 'm1', status: 'ACTIVE' as never }),
        record({ id: 'm2', status: 'ON_HOLD' as never }),
        record({ id: 'm3', status: 'ENTERED_IN_ERROR' as never }),
      ],
    });

    const result = await getPatientMedications('p-1');

    expect(result.map(r => r.status)).toEqual(['active', 'on-hold', 'entered-in-error']);
  });

  it('maps a single record on getMedication', async () => {
    (api.get as any).mockResolvedValue({ data: record({ status: 'CANCELLED' as never }) });
    const result = await getMedication('med-1');
    expect(result.status).toBe('cancelled');
  });

  it('sends the canonical uppercase enum on create and maps the response back', async () => {
    (api.post as any).mockResolvedValue({ data: record({ status: 'ON_HOLD' as never }) });

    const result = await addPatientMedication('p-1', record({ status: 'on-hold' }));

    expect(api.post).toHaveBeenCalledWith(
      '/medications/patient/p-1',
      expect.objectContaining({ status: 'ON_HOLD' }),
    );
    expect(result.status).toBe('on-hold');
  });

  it('sends the canonical enum on update', async () => {
    (api.put as any).mockResolvedValue({
      data: record({ status: 'ENTERED_IN_ERROR' as never }),
    });

    const result = await updatePatientMedication('med-1', { status: 'entered-in-error' });

    expect(api.put).toHaveBeenCalledWith(
      '/medications/med-1',
      expect.objectContaining({ status: 'ENTERED_IN_ERROR' }),
    );
    expect(result.status).toBe('entered-in-error');
  });

  it('normalizes nested records in medication usage', async () => {
    (api.get as any).mockResolvedValue({
      data: [
        {
          medication: record({ status: 'STOPPED' as never }),
          patient: { id: 'p-1', name: { text: 'Doe' } },
        },
      ],
    });

    const [usage] = await getMedicationUsage('catalog-1');
    expect(usage.medication.status).toBe('stopped');
  });
});
