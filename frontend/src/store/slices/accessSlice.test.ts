import { beforeEach, describe, expect, it, vi } from 'vitest';

// The mirror hydrates through the shared axios instance — stub it per-test.
vi.mock('../../api/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import api from '../../api/axios';
import { useAccessStore, type MeAccessPayload } from './accessSlice';

const PAYLOAD: MeAccessPayload = {
  user: { id: 'u1', email: 'user@local', full_name: 'User One', role: 'MANAGER' },
  roles: ['MANAGER'],
  permissions: ['patient:read', 'patient:write', 'clinical:read', 'settings:read'],
  tenant: { id: 't1', name: 'Test Tenant' },
  patient_grants: [
    {
      id: 'g1',
      patient_id: 'p9',
      patient_name: 'Maria Papadopoulos',
      permission: 'read',
      source: 'care_team',
      status: 'active',
      granted_at: '2026-11-10T00:00:00Z',
      accepted_at: '2026-11-10T00:00:00Z',
      expires_at: null,
    },
  ],
  pending_grants: [
    {
      id: 'g2',
      patient_id: 'p8',
      patient_name: 'Nikos Antoniou',
      permission: 'write',
      source: 'patient',
      status: 'pending',
      granted_at: '2026-11-10T00:00:00Z',
      accepted_at: null,
      expires_at: null,
    },
  ],
};

function resetStore() {
  useAccessStore.setState({
    permissions: new Set<string>(),
    roles: [],
    tenant: null,
    patientGrants: [],
    pendingGrants: [],
    isHydrated: false,
    hasError: false,
  });
}

describe('accessSlice (/me/access mirror, ADR 0054)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetStore();
    vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
  });

  it('hydrates permissions, roles, tenant and grants from /me/access', async () => {
    await useAccessStore.getState().hydrate();

    expect(api.get).toHaveBeenCalledWith('/me/access');
    const state = useAccessStore.getState();
    expect(state.isHydrated).toBe(true);
    expect(state.hasError).toBe(false);
    expect(state.roles).toEqual(['MANAGER']);
    expect(state.tenant).toEqual({ id: 't1', name: 'Test Tenant' });
    expect(state.permissions).toBeInstanceOf(Set);
    expect([...state.permissions].sort()).toEqual(
      ['clinical:read', 'patient:read', 'patient:write', 'settings:read'].sort(),
    );
    expect(state.patientGrants).toEqual(PAYLOAD.patient_grants);
    expect(state.pendingGrants).toEqual(PAYLOAD.pending_grants);
  });

  it('answers can() / canAny() from the hydrated permission set', async () => {
    const state = useAccessStore.getState();
    expect(state.can('patient:read')).toBe(false);

    await useAccessStore.getState().hydrate();

    expect(useAccessStore.getState().can('patient:read')).toBe(true);
    expect(useAccessStore.getState().can('user:read')).toBe(false);
    expect(useAccessStore.getState().canAny('user:read', 'patient:write')).toBe(true);
    expect(useAccessStore.getState().canAny('user:read', 'user:invite')).toBe(false);
  });

  it('canPatient ranks the incoming grants for that patient (read < write < admin)', async () => {
    await useAccessStore.getState().hydrate();
    const state = useAccessStore.getState();
    expect(state.canPatient('p9', 'read')).toBe(true);
    expect(state.canPatient('p9', 'write')).toBe(false);
    expect(state.canPatient('p9', 'admin')).toBe(false);
    // No grant reaches this patient.
    expect(state.canPatient('p404', 'read')).toBe(false);
    // Unknown permission levels fail closed.
    expect(state.canPatient('p9', 'superuser' as string)).toBe(false);
  });

  it('canPatient ignores settled (revoked/expired) or past-expiry grants', async () => {
    await useAccessStore.getState().hydrate();
    const future = new Date(Date.now() + 86_400_000).toISOString();
    const past = new Date(Date.now() - 86_400_000).toISOString();
    // A stale mirror can still carry settled rows — they must never count.
    useAccessStore.setState({
      patientGrants: [
        { id: 'g1', patient_id: 'p1', permission: 'admin', source: 'care_team', status: 'active', expires_at: null },
        { id: 'g2', patient_id: 'p2', permission: 'admin', source: 'care_team', status: 'revoked', expires_at: null },
        { id: 'g3', patient_id: 'p3', permission: 'admin', source: 'care_team', status: 'expired', expires_at: past },
        { id: 'g4', patient_id: 'p4', permission: 'admin', source: 'care_team', status: 'active', expires_at: past },
        { id: 'g5', patient_id: 'p5', permission: 'admin', source: 'care_team', status: 'active', expires_at: future },
      ],
    });
    const state = useAccessStore.getState();
    expect(state.canPatient('p1', 'read')).toBe(true);
    expect(state.canPatient('p2', 'read')).toBe(false);
    expect(state.canPatient('p3', 'read')).toBe(false);
    expect(state.canPatient('p4', 'read')).toBe(false);
    expect(state.canPatient('p5', 'read')).toBe(true);
  });

  it('coalesces concurrent hydrate calls into one fetch', async () => {
    await Promise.all([
      useAccessStore.getState().hydrate(),
      useAccessStore.getState().hydrate(),
      useAccessStore.getState().hydrate(),
    ]);
    expect(api.get).toHaveBeenCalledTimes(1);
  });

  it('fails closed on a hydrate error — every permission denied', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('network down'));
    await useAccessStore.getState().hydrate();

    const state = useAccessStore.getState();
    expect(state.isHydrated).toBe(true);
    expect(state.hasError).toBe(true);
    expect(state.permissions.size).toBe(0);
    expect(state.can('patient:read')).toBe(false);
    expect(state.canAny('patient:read', 'clinical:read')).toBe(false);
    expect(state.tenant).toBeNull();
  });

  it('clear() wipes the mirror (logout / dead session)', async () => {
    await useAccessStore.getState().hydrate();
    expect(useAccessStore.getState().isHydrated).toBe(true);

    useAccessStore.getState().clear();

    const state = useAccessStore.getState();
    expect(state.isHydrated).toBe(false);
    expect(state.hasError).toBe(false);
    expect(state.permissions.size).toBe(0);
    expect(state.roles).toEqual([]);
    expect(state.tenant).toBeNull();
    expect(state.patientGrants).toEqual([]);
    expect(state.pendingGrants).toEqual([]);
  });
});
