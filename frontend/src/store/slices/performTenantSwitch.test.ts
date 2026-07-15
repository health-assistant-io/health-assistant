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

  it('persists scoped tokens, records the switch, and calls onTokensUpdated exactly once', async () => {
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

    const onTokensUpdated = vi.fn();

    const tenant = await performTenantSwitch('t1', onTokensUpdated);

    // Returns the scoped tenant.
    expect(tenant).toEqual(scopedTenant);

    // Scoped tokens persisted to localStorage.
    expect(localStorage.getItem('accessToken')).toBe('scoped-access');
    expect(localStorage.getItem('refreshToken')).toBe('scoped-refresh');

    // Audit D5: the token callback must fire exactly once. A previous version
    // duplicated the call (harmless today, but a real defect — any side-effect
    // in the callback would double-apply).
    expect(onTokensUpdated).toHaveBeenCalledTimes(1);
    expect(onTokensUpdated).toHaveBeenCalledWith('scoped-access', 'scoped-refresh');

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
