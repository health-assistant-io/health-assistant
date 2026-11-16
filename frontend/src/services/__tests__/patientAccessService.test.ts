import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../../api/axios';
import {
  listPatientAccess,
  createPatientAccess,
  updatePatientAccess,
  revokePatientAccess,
  getMyGrants,
  acceptMyGrant,
  declineMyGrant,
  grantSatisfies,
} from '../patientAccessService';

const GRANT = {
  id: 'g1',
  patient_id: 'p1',
  grantee_user_id: 'u2',
  permission: 'read',
  source: 'care_team',
  status: 'active',
};

describe('patientAccessService (ADR 0054 phase 2 surface)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists a patient ledger from GET /patients/{id}/access', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [GRANT] } });

    const items = await listPatientAccess('p1');

    expect(api.get).toHaveBeenCalledWith('/patients/p1/access');
    expect(items).toEqual([GRANT]);
  });

  it('creates a grant via POST /patients/{id}/access', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { ...GRANT, id: 'g2' } });
    const payload = { grantee_user_id: 'u2', permission: 'write' as const };

    const grant = await createPatientAccess('p1', payload);

    expect(api.post).toHaveBeenCalledWith('/patients/p1/access', payload);
    expect(grant.id).toBe('g2');
  });

  it('updates a grant via PATCH /patients/{id}/access/{grant_id}', async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: GRANT });
    const payload = { expires_at: '2027-01-01T00:00:00Z' };

    await updatePatientAccess('p1', 'g1', payload);

    expect(api.patch).toHaveBeenCalledWith('/patients/p1/access/g1', payload);
  });

  it('revokes via DELETE /patients/{id}/access/{grant_id}', async () => {
    vi.mocked(api.delete).mockResolvedValue({ data: { ...GRANT, status: 'revoked' } });

    await revokePatientAccess('p1', 'g1');

    expect(api.delete).toHaveBeenCalledWith('/patients/p1/access/g1');
  });

  it('reads my grants from /me/access/grants with empty-safe lists', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { active: [GRANT] } });

    const data = await getMyGrants();

    expect(api.get).toHaveBeenCalledWith('/me/access/grants');
    expect(data.active).toEqual([GRANT]);
    expect(data.pending).toEqual([]);
  });

  it('accepts and declines pending shares (decline carries the optional reason)', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { id: 'g1', status: 'active' } });

    await acceptMyGrant('g1');
    expect(api.post).toHaveBeenCalledWith('/me/access/grants/g1/accept');

    await declineMyGrant('g1', 'not my patient');
    expect(api.post).toHaveBeenCalledWith('/me/access/grants/g1/decline', {
      reason: 'not my patient',
    });

    await declineMyGrant('g1');
    expect(api.post).toHaveBeenCalledWith('/me/access/grants/g1/decline', null);
  });

  it('ranks permissions read < write < admin', () => {
    expect(grantSatisfies('read', 'read')).toBe(true);
    expect(grantSatisfies('read', 'write')).toBe(false);
    expect(grantSatisfies('write', 'read')).toBe(true);
    expect(grantSatisfies('admin', 'admin')).toBe(true);
    expect(grantSatisfies('admin', 'write')).toBe(true);
    expect(grantSatisfies(null, 'read')).toBe(false);
    expect(grantSatisfies(undefined, 'read')).toBe(false);
  });
});
