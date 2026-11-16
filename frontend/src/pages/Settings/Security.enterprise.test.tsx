import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in (opts as object)) {
      return String((opts as { defaultValue: string }).defaultValue);
    }
    return key;
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const { showConfirmation } = vi.hoisted(() => ({
  showConfirmation: vi.fn((options: { onConfirm: () => void }) => options.onConfirm()),
}));
vi.mock('../../store/slices/uiSlice', () => {
  const state = {
    showConfirmation,
    setPageHeaderConfig: vi.fn(),
    pageHeaderConfig: null,
  };
  const useUIStore = (selector?: (value: typeof state) => unknown) =>
    selector ? selector(state) : state;
  useUIStore.getState = () => state;
  return { useUIStore };
});

vi.mock('../../store/slices/authSlice', () => ({
  useAuthStore: (selector: (state: { logout: () => Promise<void> }) => unknown) =>
    selector({ logout: vi.fn().mockResolvedValue(undefined) }),
}));

vi.mock('../../services/sessionService', () => ({
  listMySessions: vi.fn(),
  revokeMySession: vi.fn(),
  revokeOtherSessions: vi.fn(),
  changeMyPassword: vi.fn(),
}));

vi.mock('../../services/mfaService', () => ({
  getMyMfa: vi.fn(),
  enrollMyMfa: vi.fn(),
  confirmMyMfa: vi.fn(),
  disableMyMfa: vi.fn(),
}));

vi.mock('../../services/passkeyService', () => ({
  listMyPasskeys: vi.fn(),
  passkeysSupported: vi.fn(() => true),
  registerPasskey: vi.fn(),
  deleteMyPasskey: vi.fn(),
}));

vi.mock('../../services/apiTokenService', () => ({
  listApiTokens: vi.fn(),
  getApiTokenPermissions: vi.fn(),
  createApiToken: vi.fn(),
  rotateApiToken: vi.fn(),
  revokeApiToken: vi.fn(),
}));

vi.mock('../../services/elevationService', () => ({
  listMyElevations: vi.fn(),
}));

import { toast } from 'react-toastify';
import Security from './Security';
import {
  listMySessions,
  revokeMySession,
  revokeOtherSessions,
} from '../../services/sessionService';
import { getMyMfa } from '../../services/mfaService';
import {
  listMyPasskeys,
  registerPasskey,
} from '../../services/passkeyService';
import {
  createApiToken,
  getApiTokenPermissions,
  listApiTokens,
} from '../../services/apiTokenService';
import { listMyElevations } from '../../services/elevationService';
import { useAccessStore } from '../../store/slices/accessSlice';

const sessionsMock = listMySessions as unknown as Mock;
const mfaMock = getMyMfa as unknown as Mock;
const passkeysMock = listMyPasskeys as unknown as Mock;
const registerMock = registerPasskey as unknown as Mock;
const tokensMock = listApiTokens as unknown as Mock;
const catalogMock = getApiTokenPermissions as unknown as Mock;
const createTokenMock = createApiToken as unknown as Mock;
const elevationsMock = listMyElevations as unknown as Mock;
const revokeOthersMock = revokeOtherSessions as unknown as Mock;
const revokeSessionMock = revokeMySession as unknown as Mock;

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
  return writeText;
}

const TWO_SESSIONS = [
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
    client_label: 'Pixel',
    ip: '198.51.100.4',
    user_agent: 'HealthApp/2.1',
    last_seen_at: '2026-11-11T09:00:00Z',
    created_at: '2026-11-09T09:00:00Z',
    expires_at: null,
    absolute_expires_at: null,
    revoked_at: null,
    current: false,
  },
];

function seedAccess() {
  useAccessStore.setState({
    permissions: new Set<string>(),
    roles: ['ADMIN'],
    tenant: { id: 't1', name: 'Clinic' },
    patientGrants: [],
    pendingGrants: [],
    isHydrated: true,
    hasError: false,
  });
}

describe('Security — passkeys, sessions and API tokens (program 12C)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedAccess();
    mfaMock.mockResolvedValue({
      enabled: true,
      enforced: false,
      pending: false,
      totp_enabled: true,
      passkey_enabled: true,
      passkey_count: 1,
    });
    sessionsMock.mockResolvedValue([]);
    passkeysMock.mockResolvedValue([]);
    tokensMock.mockResolvedValue([]);
    catalogMock.mockResolvedValue({
      permissions: [{ permission: 'patient:read', scope: 'own' }],
      patient_scopes: ['all', 'granted', 'none'],
    });
    elevationsMock.mockResolvedValue([]);
  });

  it('renders the device facts and signs out the other sessions', async () => {
    sessionsMock.mockResolvedValue(TWO_SESSIONS);
    revokeOthersMock.mockResolvedValue(1);

    const user = userEvent.setup();
    render(<Security />);

    expect(await screen.findByText(/203\.0\.113\.9/)).toBeInTheDocument();
    expect(screen.getByText(/198\.51\.100\.4/)).toBeInTheDocument();
    expect(screen.getByText(/Firefox\/128/)).toBeInTheDocument();
    // Current-session badge comes from the library row; every row keeps a
    // per-device revoke action whose accessible name carries the device.
    expect(screen.getByText('This device')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revoke — Firefox on Linux' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revoke — Pixel' })).toBeInTheDocument();
    expect(screen.getAllByText(/^Signed in: /)[0]).toBeInTheDocument();

    await user.click(screen.getByTestId('sessions-revoke-others'));
    await waitFor(() => expect(revokeOthersMock).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalled();
  });

  it('revokes a single device and reloads the list', async () => {
    sessionsMock.mockResolvedValue(TWO_SESSIONS);
    revokeSessionMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    render(<Security />);

    await user.click(await screen.findByRole('button', { name: 'Revoke — Pixel' }));

    await waitFor(() => expect(revokeSessionMock).toHaveBeenCalledWith('s2'));
    expect(toast.success).toHaveBeenCalledWith('Session revoked');
    await waitFor(() => expect(sessionsMock).toHaveBeenCalledTimes(2));
  });

  it('hides "sign out others" when there is no other session', async () => {
    sessionsMock.mockResolvedValue([TWO_SESSIONS[0]]);

    render(<Security />);

    expect(await screen.findByText('Firefox on Linux')).toBeInTheDocument();
    expect(screen.queryByTestId('sessions-revoke-others')).not.toBeInTheDocument();
  });

  it('lists passkeys and registers a named credential', async () => {
    passkeysMock.mockResolvedValue([
      {
        id: 'p1',
        name: 'MacBook Touch ID',
        transports: ['internal'],
        aaguid: null,
        backup_eligible: true,
        backup_state: false,
        created_at: '2026-11-01T10:00:00Z',
        last_used_at: '2026-11-10T10:00:00Z',
        disabled: false,
      },
    ]);
    registerMock.mockResolvedValue({ id: 'p2' });

    const user = userEvent.setup();
    render(<Security />);

    expect(await screen.findByText('MacBook Touch ID')).toBeInTheDocument();
    expect(screen.getByText(/Last used/)).toBeInTheDocument();

    await user.click(screen.getByTestId('passkey-add'));
    const nameInput = screen.getByLabelText('Passkey name');
    await user.clear(nameInput);
    await user.type(nameInput, 'YubiKey');
    await user.click(screen.getByRole('button', { name: 'Register' }));

    await waitFor(() => expect(registerMock).toHaveBeenCalledWith('YubiKey'));
    expect(toast.success).toHaveBeenCalled();
  });

  it('creates an API token from the permission catalog and shows the secret once', async () => {
    createTokenMock.mockResolvedValue({
      token: 'ha_pat_abc_secret',
      api_token: {
        id: 'tok1',
        name: 'Backup script',
        permissions: ['patient:read'],
        patient_scope: 'all',
        patient_ids: [],
        token_prefix: 'ha_pat_abc',
        expires_at: null,
        last_used_at: null,
        revoked_at: null,
      },
    });

    const user = userEvent.setup();
    render(<Security />);

    await user.click(await screen.findByTestId('api-token-add'));
    await user.type(screen.getByLabelText('Name'), 'Backup script');
    await user.click(screen.getByTestId('api-token-permission-patient:read'));
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(createTokenMock).toHaveBeenCalledWith({
        name: 'Backup script',
        permissions: ['patient:read'],
        patient_scope: 'all',
        patient_ids: [],
        expires_at: null,
      }),
    );
    expect(await screen.findByText('ha_pat_abc_secret')).toBeInTheDocument();
    const secretBlock = screen.getByTestId('api-token-secret');
    expect(secretBlock).toHaveAttribute('data-as', 'secret-once-block');
    expect(
      screen.getByText('This secret is shown once. Store it securely — it cannot be recovered.'),
    ).toBeInTheDocument();

    const writeText = stubClipboard();
    await user.click(within(secretBlock).getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('ha_pat_abc_secret');
    expect(await within(secretBlock).findByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });
});
