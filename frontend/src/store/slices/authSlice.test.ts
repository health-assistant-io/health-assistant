import { describe, it, expect, vi, beforeEach } from 'vitest';

// validateSession hits the network — stub it per-test.
vi.mock('../../utils/auth', () => ({
  validateSession: vi.fn(),
  clearAuthData: vi.fn(async () => undefined),
}));

// The access mirror (ADR 0054) — observe hydrate/clear wiring, not HTTP.
// One stable mock instance: authSlice calls `useAccessStore.getState()`,
// so the assertions must see the very same vi.fn objects.
const { accessMocks } = vi.hoisted(() => ({
  accessMocks: {
    hydrate: vi.fn(async () => undefined),
    clear: vi.fn(),
  },
}));
vi.mock('./accessSlice', () => ({
  useAccessStore: { getState: () => accessMocks },
}));

// The cookie-mode axios instance (logout POST + CSRF interceptor).
vi.mock('../../api/axios', () => ({
  default: {
    get: vi.fn(async () => ({ data: {} })),
    post: vi.fn(async () => ({ status: 200 })),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import { validateSession } from '../../utils/auth';
import { useAuthStore } from './authSlice';
import api from '../../api/axios';

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
    // The permission mirror dies with the session (ADR 0054).
    expect(accessMocks.clear).toHaveBeenCalled();
  });

  it('initialize() hydrates the access mirror for a live session', async () => {
    vi.mocked(validateSession).mockResolvedValue({
      valid: true,
      user_id: 'u1',
      role: 'ADMIN',
      tenant_id: 't1',
      auth_mode: 'password',
      switched: false,
      original_tenant_id: null,
    });

    await useAuthStore.getState().initialize();

    expect(accessMocks.hydrate).toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it('login() hydrates the fresh session permission set', async () => {
    vi.mocked(validateSession).mockResolvedValue({
      valid: true,
      user_id: 'u2',
      role: 'USER',
      tenant_id: 't1',
      auth_mode: 'password',
      switched: false,
      original_tenant_id: null,
    });

    useAuthStore.getState().login();

    // The re-sync (and the hydrate with it) is async — flush it.
    await vi.waitFor(() => {
      expect(accessMocks.hydrate).toHaveBeenCalled();
    });
  });

  it('resetSession() clears the access mirror without a server call', () => {
    useAuthStore.getState().resetSession();
    expect(accessMocks.clear).toHaveBeenCalled();
  });

  it('logout() clears the access mirror', async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 200 });
    // jsdom cannot navigate — replace location so the post-logout redirect
    // (a store contract since plan 16) stays silent in the test run. Only
    // this test in the file touches it; vitest re-creates jsdom per file.
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: { href: '/dashboard' },
    });
    await useAuthStore.getState().logout();
    expect(api.post).toHaveBeenCalledWith('/auth/logout');
    expect(accessMocks.clear).toHaveBeenCalled();
  });
});
