import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Security from './Security';
import api from '../../api/axios';
import { toast } from 'react-toastify';

vi.mock('../../api/axios', () => ({
  default: {
    get: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

vi.mock('../../store/slices/authSlice', () => ({
  useAuthStore: (selector: (state: { logout: () => Promise<void> }) => unknown) =>
    selector({ logout: vi.fn().mockResolvedValue(undefined) }),
}));

const apiGet = api.get as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;
const apiDelete = api.delete as unknown as Mock;

function setup() {
  return render(<Security />);
}

/** Page data loads (MFA status + sessions) through the same mocked axios. */
async function setupLoaded() {
  setup();
  await screen.findByText('Two-factor authentication (TOTP)');
  expect(await screen.findByText('No other active sessions.')).toBeTruthy();
}

async function openPasswordModal() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Change' }));
  await screen.findByLabelText('Current password');
  return user;
}

async function fillAndPasswordSubmit(
  user: ReturnType<typeof userEvent.setup>,
  { current, next, confirm }: { current: string; next: string; confirm: string },
) {
  await user.type(screen.getByLabelText('Current password'), current);
  await user.type(screen.getByLabelText('New password'), next);
  await user.type(screen.getByLabelText('Confirm new password'), confirm);
  await user.click(screen.getByRole('button', { name: 'Update password' }));
}

describe('Security page — Change Password', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiGet.mockImplementation((url: string) =>
      Promise.resolve(
        url === '/me/sessions'
          ? { data: [] }
          : { data: { enabled: false, enforced: false, pending: false } },
      ),
    );
  });

  it('opens the change-password modal from the card button', async () => {
    await setupLoaded();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Change' }));
    expect(await screen.findByLabelText('Current password')).toBeTruthy();
    expect(screen.getByLabelText('New password')).toBeTruthy();
    expect(screen.getByLabelText('Confirm new password')).toBeTruthy();
  });

  it('blocks submission when the confirmation does not match', async () => {
    await setupLoaded();
    const user = await openPasswordModal();
    await fillAndPasswordSubmit(user, {
      current: 'old-password',
      next: 'new-secure-password',
      confirm: 'different-password',
    });
    expect(screen.getByText('Passwords do not match.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Update password' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Update password' }));
    expect(apiPatch).not.toHaveBeenCalled();
  });

  it('posts the current + new password to /me/password and closes on success', async () => {
    apiPatch.mockResolvedValue({ status: 204 });
    await setupLoaded();
    const user = await openPasswordModal();
    await fillAndPasswordSubmit(user, {
      current: 'old-password',
      next: 'new-secure-password',
      confirm: 'new-secure-password',
    });
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith('/me/password', {
        current_password: 'old-password',
        new_password: 'new-secure-password',
      }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByLabelText('Current password')).toBeNull());
  });

  it('shows the 400 detail when the current password is wrong', async () => {
    apiPatch.mockRejectedValue({
      response: { status: 400, data: { detail: 'Current password is incorrect' } },
    });
    await setupLoaded();
    const user = await openPasswordModal();
    await fillAndPasswordSubmit(user, {
      current: 'wrong-password',
      next: 'new-secure-password',
      confirm: 'new-secure-password',
    });
    expect(await screen.findByText('Current password is incorrect')).toBeTruthy();
    // The modal stays open so the user can retry.
    expect(screen.getByLabelText('Current password')).toBeTruthy();
  });
});

const SESSIONS = [
  {
    id: 's1',
    client_label: 'Firefox on Linux',
    ip: '203.0.113.9',
    user_agent: 'Firefox/128',
    last_seen_at: '2026-11-11T10:00:00Z',
    created_at: '2026-11-10T09:00:00Z',
    expires_at: '2026-11-17T09:00:00Z',
    absolute_expires_at: null,
    revoked_at: null,
    current: true,
  },
  {
    id: 's2',
    client_label: '',
    ip: null,
    user_agent: null,
    last_seen_at: null,
    created_at: '2026-11-09T09:00:00Z',
    expires_at: null,
    absolute_expires_at: null,
    revoked_at: null,
    current: false,
  },
];

describe('Security page — active sessions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiGet.mockImplementation((url: string) =>
      Promise.resolve(
        url === '/me/sessions'
          ? { data: SESSIONS }
          : { data: { enabled: false, enforced: false, pending: false } },
      ),
    );
  });

  it('renders the library session list with the current badge and device-labelled revoke actions', async () => {
    setup();

    const currentRow = await screen.findByText('Firefox on Linux');
    expect(currentRow.closest('[data-as="account-session-row"]')).toHaveAttribute(
      'data-current',
      'true',
    );
    expect(screen.getByText('This device')).toBeTruthy();
    expect(screen.getByText('203.0.113.9')).toBeTruthy();
    expect(screen.getByText('Firefox/128')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Revoke — Firefox on Linux' })).toBeTruthy();
    // A session without a client label falls back to the unknown-device copy,
    // kept recognizable and made unique with the short session id.
    expect(screen.getByText('Unknown device · s2')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Revoke — Unknown device · s2' })).toBeTruthy();
    expect(screen.getByTestId('sessions-revoke-others')).toBeTruthy();
  });

  it('gives label-less sessions distinct revoke accessible names', async () => {
    const labelLess = [
      { ...SESSIONS[1], id: 's2' },
      { ...SESSIONS[1], id: 's3', ip: '198.51.100.4' },
      { ...SESSIONS[1], id: 's4', ip: '198.51.100.4' },
    ];
    apiGet.mockImplementation((url: string) =>
      Promise.resolve(
        url === '/me/sessions'
          ? { data: labelLess }
          : { data: { enabled: false, enforced: false, pending: false } },
      ),
    );
    setup();

    const withoutIp = await screen.findByRole('button', { name: 'Revoke — Unknown device · s2' });
    const sharedIpA = screen.getByRole('button', { name: 'Revoke — 198.51.100.4 · s3' });
    const sharedIpB = screen.getByRole('button', { name: 'Revoke — 198.51.100.4 · s4' });
    const names = [withoutIp, sharedIpA, sharedIpB].map((button) =>
      button.getAttribute('aria-label'),
    );
    expect(new Set(names).size).toBe(3);
    expect(screen.getAllByRole('button', { name: /^Revoke — / })).toHaveLength(3);
  });

  it('revokes the chosen session and reloads the list', async () => {
    apiDelete.mockResolvedValue({ status: 204 });
    setup();

    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Revoke — Unknown device · s2' }));

    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/me/sessions/s2'));
    expect(toast.success).toHaveBeenCalledWith('Session revoked');
    await waitFor(() =>
      expect(apiGet.mock.calls.filter(([url]) => url === '/me/sessions')).toHaveLength(2),
    );
  });
});
