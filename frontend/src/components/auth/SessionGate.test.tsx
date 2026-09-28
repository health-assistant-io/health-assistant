import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The gate boots through the auth store, which hits the network via
// validateSession — stub it per-test (same pattern as authSlice.test.ts).
vi.mock('../../utils/auth', () => ({
  validateSession: vi.fn(),
  clearAuthData: vi.fn(async () => undefined),
}));

// The login node probes /auth/setup-status and demo-login through the
// shared axios instance; the gate listens for UNAUTHENTICATED_EVENT.
vi.mock('../../api/axios', () => {
  const api = {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  };
  return {
    default: api,
    UNAUTHENTICATED_EVENT: 'nx:unauthenticated',
    csrfToken: () => null,
    refreshSession: vi.fn(async () => false),
  };
});

import { validateSession } from '../../utils/auth';
import { default as api } from '../../api/axios';
import { useAuthStore } from '../../store/slices/authSlice';

import { SessionGate } from './SessionGate';

/** §10 claim shape the store's initialize() expects from a live session. */
const CLAIMS = {
  valid: true,
  user_id: 'u1',
  email: 'user@local',
  role: 'ADMIN',
  tenant_id: 't1',
  auth_mode: 'password',
  switched: false,
  original_tenant_id: null,
};

function renderGate() {
  return render(
    <MemoryRouter>
      <SessionGate>
        <div data-testid="app" />
      </SessionGate>
    </MemoryRouter>,
  );
}

describe('SessionGate (shared AuthGate wiring, plan 16 closeout)', () => {
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
    // Default: an initialized, non-demo instance.
    vi.mocked(api.get).mockResolvedValue({
      data: { initialized: true, demo_mode: false },
    });
  });

  it('renders children once the boot flow establishes a session', async () => {
    vi.mocked(validateSession).mockResolvedValue(CLAIMS);

    renderGate();

    await waitFor(() => expect(screen.getByTestId('app')).toBeInTheDocument());
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().claims?.user_id).toBe('u1');
  });

  it('lands on the login surface when the cookie session is dead', async () => {
    vi.mocked(validateSession).mockResolvedValue(null);

    renderGate();

    await waitFor(() =>
      expect(document.querySelector('input#email')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('app')).not.toBeInTheDocument();
  });

  it('composes the first-run setup wizard inside the login node', async () => {
    vi.mocked(validateSession).mockResolvedValue(null);
    vi.mocked(api.get).mockResolvedValue({
      data: { initialized: false, setup_token_required: false, token_mode: 'disabled' },
    });

    renderGate();

    // The setup form — not the login form — and no authenticated app.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /create admin account/i })).toBeInTheDocument(),
    );
    // Setup-only field; the login form has no tenant-name input.
    expect(document.querySelector('input#tenant_name')).not.toBeNull();
    expect(screen.queryByTestId('app')).not.toBeInTheDocument();
  });

  it('auto-signs-in in demo mode and re-boots the gate into the app', async () => {
    // Boot 1 finds no session; every later boot/claim-sync sees the demo
    // session demo-login minted.
    vi.mocked(validateSession)
      .mockResolvedValueOnce(null)
      .mockResolvedValue({ ...CLAIMS, auth_mode: 'demo', role: 'USER' });
    vi.mocked(api.get).mockResolvedValue({ data: { demo_mode: true } });
    vi.mocked(api.post).mockResolvedValue({ status: 200 }); // demo-login OK

    renderGate();

    // The successful demo-login bumps resetKey → boot 2 sees the session.
    await waitFor(() => expect(screen.getByTestId('app')).toBeInTheDocument());
    expect(api.post).toHaveBeenCalledWith('/auth/demo-login');
    expect(vi.mocked(validateSession).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(useAuthStore.getState().isDemoMode).toBe(true);
  });

  it('re-runs the boot flow when a mid-session 401 dispatches the unauthenticated event', async () => {
    vi.mocked(validateSession).mockResolvedValue(CLAIMS);

    renderGate();
    await waitFor(() => expect(screen.getByTestId('app')).toBeInTheDocument());

    vi.mocked(validateSession).mockResolvedValue(null);
    window.dispatchEvent(new Event('nx:unauthenticated'));

    await waitFor(() =>
      expect(document.querySelector('input#email')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('app')).not.toBeInTheDocument();
    expect(vi.mocked(validateSession).mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
