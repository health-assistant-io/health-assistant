import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../../api/axios';

describe('tenantSwitchSlice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('enterTenant records the switch WITHOUT storing tokens (§10)', async () => {
    const { useTenantSwitchStore } = await import('./tenantSwitchSlice');
    useTenantSwitchStore.getState().enterTenant(
      { id: 't1', name: 'Acme', slug: 'acme', is_active: true, settings: {} } as any,
      'original-tenant-id'
    );

    const state = useTenantSwitchStore.getState();
    expect(state.switched).toBe(true);
    expect(state.originalTenantId).toBe('original-tenant-id');
    expect(state.scopedTenant?.id).toBe('t1');
    // §10: the cookie triple is the credential — no token keys anywhere.
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(localStorage.getItem('originalAccessToken')).toBeNull();
    expect(localStorage.getItem('originalRefreshToken')).toBeNull();
  });

  it('exitTenant calls the backend exit endpoint and clears the switched state', async () => {
    api.post = vi.fn().mockResolvedValue({
      status: 200,
      data: {
        access_token: 'restored-access', // §9 body tokens — ignored by the browser
        refresh_token: 'restored-refresh',
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
    // §10: the restored session lives in the HttpOnly cookies the backend
    // re-stamped — nothing lands in localStorage.
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('originalAccessToken')).toBeNull();
    expect(useTenantSwitchStore.getState().switched).toBe(false);
  });

  it('exitTenant rejects on backend failure so the UI can offer re-login', async () => {
    api.post = vi.fn().mockRejectedValue(new Error('network down')) as any;

    const { useTenantSwitchStore } = await import('./tenantSwitchSlice');
    useTenantSwitchStore.getState().enterTenant(
      { id: 't1', name: 'Acme', slug: 'acme', is_active: true, settings: {} } as any,
      'orig-tenant'
    );

    await expect(useTenantSwitchStore.getState().exitTenant()).rejects.toBeDefined();
    // State is still cleared — the stale banner must not linger.
    expect(useTenantSwitchStore.getState().switched).toBe(false);
    expect(localStorage.getItem('accessToken')).toBeNull();
  });

  it('syncFromToken syncs switched state from session claims', async () => {
    const { useTenantSwitchStore } = await import('./tenantSwitchSlice');
    useTenantSwitchStore.getState().clear();

    // Claims shape from GET /auth/validate (the HttpOnly JWT is not
    // decodable in the browser — §10).
    useTenantSwitchStore.getState().syncFromToken({
      switched: true,
      tenant_id: 'scoped-t',
      original_tenant_id: 'orig-t',
    });

    const state = useTenantSwitchStore.getState();
    expect(state.switched).toBe(true);
    expect(state.originalTenantId).toBe('orig-t');
    expect(state.scopedTenant?.id).toBe('scoped-t');

    useTenantSwitchStore.getState().syncFromToken({ switched: false });
    expect(useTenantSwitchStore.getState().switched).toBe(false);
  });
});
