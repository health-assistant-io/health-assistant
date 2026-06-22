import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../../api/axios';

describe('tenantSwitchSlice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('enterTenant saves the current tokens to original-token storage', async () => {
    localStorage.setItem('accessToken', 'current-access');
    localStorage.setItem('refreshToken', 'current-refresh');

    const { useTenantSwitchStore } = await import('./tenantSwitchSlice');
    useTenantSwitchStore.getState().enterTenant(
      { id: 't1', name: 'Acme', slug: 'acme', is_active: true, settings: {} } as any,
      'original-tenant-id'
    );

    const state = useTenantSwitchStore.getState();
    expect(state.switched).toBe(true);
    expect(state.originalTenantId).toBe('original-tenant-id');
    expect(state.scopedTenant?.id).toBe('t1');
    expect(localStorage.getItem('originalAccessToken')).toBe('current-access');
    expect(localStorage.getItem('originalRefreshToken')).toBe('current-refresh');
  });

  it('exitTenant calls the backend exit endpoint and clears storage', async () => {
    localStorage.setItem('originalAccessToken', 'orig-access');
    localStorage.setItem('originalRefreshToken', 'orig-refresh');

    api.post = vi.fn().mockResolvedValue({
      data: {
        access_token: 'new-access',
        refresh_token: 'new-refresh',
        token_type: 'bearer',
        expires_in: 3600,
        scoped_tenant_id: 'orig-tenant',
        original_tenant_id: 'orig-tenant',
        tenant: { id: 'orig-tenant', name: 'Orig' },
      },
    }) as any;

    const { useTenantSwitchStore } = await import('./tenantSwitchSlice');
    // Seed switched state.
    useTenantSwitchStore.getState().enterTenant(
      { id: 't1', name: 'Acme', slug: 'acme', is_active: true, settings: {} } as any,
      'orig-tenant'
    );

    await useTenantSwitchStore.getState().exitTenant();

    expect(api.post).toHaveBeenCalledWith('/admin/tenants/exit-switch');
    expect(localStorage.getItem('accessToken')).toBe('new-access');
    expect(localStorage.getItem('originalAccessToken')).toBeNull();
    expect(useTenantSwitchStore.getState().switched).toBe(false);
  });

  it('exitTenant falls back to original tokens when backend fails', async () => {
    localStorage.setItem('originalAccessToken', 'fallback-access');
    localStorage.setItem('originalRefreshToken', 'fallback-refresh');

    api.post = vi.fn().mockRejectedValue(new Error('network down')) as any;

    const { useTenantSwitchStore } = await import('./tenantSwitchSlice');
    useTenantSwitchStore.getState().enterTenant(
      { id: 't1', name: 'Acme', slug: 'acme', is_active: true, settings: {} } as any,
      'orig-tenant'
    );

    await useTenantSwitchStore.getState().exitTenant();

    expect(localStorage.getItem('accessToken')).toBe('fallback-access');
    expect(localStorage.getItem('originalAccessToken')).toBeNull();
    expect(useTenantSwitchStore.getState().switched).toBe(false);
  });
});
