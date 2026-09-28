import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the tenant service so performTenantSwitch's contract can be asserted
// in isolation (the store/enterTenant/exitTenant behaviour is covered by the
// sibling tenantSwitchSlice.test.ts suite, which keeps the real service).
vi.mock('../../services/tenantService', () => ({
  switchIntoTenant: vi.fn(),
  exitTenantSwitch: vi.fn(),
}));

import { switchIntoTenant } from '../../services/tenantService';
import { performTenantSwitch, useTenantSwitchStore } from './tenantSwitchSlice';

describe('performTenantSwitch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useTenantSwitchStore.getState().clear();
  });

  it('records the switch without persisting any tokens (§10)', async () => {
    const scopedTenant = { id: 't1', name: 'Acme', slug: 'acme', is_active: true, settings: {} };
    vi.mocked(switchIntoTenant).mockResolvedValue({
      access_token: 'scoped-access',
      refresh_token: 'scoped-refresh',
      token_type: 'bearer',
      expires_in: 3600,
      scoped_tenant_id: 't1',
      original_tenant_id: 'orig-tenant',
      tenant: scopedTenant as any,
    });

    const tenant = await performTenantSwitch('t1');

    // Returns the scoped tenant.
    expect(tenant).toEqual(scopedTenant);

    // §10 (plan 16 H3): the browser credential is the HttpOnly cookie
    // triple the backend re-stamped — localStorage stays token-free.
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(localStorage.getItem('originalAccessToken')).toBeNull();
    expect(localStorage.getItem('originalRefreshToken')).toBeNull();

    // Switch state recorded for the banner / exit button.
    const state = useTenantSwitchStore.getState();
    expect(state.switched).toBe(true);
    expect(state.originalTenantId).toBe('orig-tenant');
    expect(state.scopedTenant?.id).toBe('t1');

    // Only one backend switch call.
    expect(switchIntoTenant).toHaveBeenCalledTimes(1);
    expect(switchIntoTenant).toHaveBeenCalledWith('t1');
  });
});
