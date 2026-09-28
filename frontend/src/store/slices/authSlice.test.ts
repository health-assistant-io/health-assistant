import { describe, it, expect, vi, beforeEach } from 'vitest';

// validateSession hits the network — stub it per-test.
vi.mock('../../utils/auth', () => ({
  validateSession: vi.fn(),
  clearAuthData: vi.fn(async () => undefined),
}));

import { validateSession } from '../../utils/auth';
import { useAuthStore } from './authSlice';

describe('authSlice (§10 cookie mode, plan 16 H3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useAuthStore.setState({
      user: null,
      claims: null,
      isAuthenticated: false,
      isDemoMode: false,
      isLoading: true,
    });
  });

  it('login() authenticates WITHOUT writing tokens to localStorage', async () => {
    vi.mocked(validateSession).mockResolvedValue({
      valid: true,
      user_id: 'u1',
      email: 'demo@local',
      role: 'ADMIN',
      tenant_id: 't1',
      auth_mode: 'password',
      switched: false,
      original_tenant_id: null,
    });

    useAuthStore.getState().login();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);

    // The claims re-sync triggered by login() is async — flush it.
    await vi.waitFor(() => {
      expect(useAuthStore.getState().claims?.user_id).toBe('u1');
    });

    // §10: identity-auth forbids token storage in Web Storage for browser
    // clients — nothing may appear under any token key.
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(Object.keys(localStorage).length).toBe(0);
  });

  it('initialize() marks the session from server-verified claims (demo mode)', async () => {
    vi.mocked(validateSession).mockResolvedValue({
      valid: true,
      user_id: 'demo-user',
      auth_mode: 'demo',
      role: 'USER',
      tenant_id: 't-demo',
      switched: false,
      original_tenant_id: null,
    });

    await useAuthStore.getState().initialize();

    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(true);
    expect(state.isDemoMode).toBe(true);
    expect(state.isLoading).toBe(false);
    expect(state.claims?.auth_mode).toBe('demo');
    expect(Object.keys(localStorage).length).toBe(0);
  });

  it('initialize() clears auth state when the cookie session is dead', async () => {
    vi.mocked(validateSession).mockResolvedValue(null);

    await useAuthStore.getState().initialize();

    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.isDemoMode).toBe(false);
    expect(state.isLoading).toBe(false);
    expect(state.claims).toBeNull();
  });
});
