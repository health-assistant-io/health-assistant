import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

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

vi.mock('../../../api/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  consumeSessionEndReason: vi.fn(),
  sessionEndReasonOf: vi.fn(),
}));

vi.mock('../../../services/mfaService', () => ({
  mfaChallengeOf: vi.fn(),
  verifyMfaChallenge: vi.fn(),
  enrollForcedMfa: vi.fn(),
}));

vi.mock('../../../store/slices/authSlice', () => ({
  useAuthStore: (selector?: (state: { login: () => void }) => unknown) => {
    const state = { login: vi.fn() };
    return selector ? selector(state) : state;
  },
}));

vi.mock('../../../store/slices/settingsSlice', () => ({
  useSettingsStore: (selector: (state: { theme: string }) => unknown) => selector({ theme: 'light' }),
}));

import api, {
  consumeSessionEndReason,
  sessionEndReasonOf,
} from '../../../api/axios';
import { mfaChallengeOf, enrollForcedMfa } from '../../../services/mfaService';
import Login from '../Login';

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;
const consumeMock = consumeSessionEndReason as unknown as Mock;
const reasonOfMock = sessionEndReasonOf as unknown as Mock;
const challengeOfMock = mfaChallengeOf as unknown as Mock;

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>
  );
}

describe('Login — tenant security policy errors (ADR 0054 phase 3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiGet.mockResolvedValue({ data: { initialized: true, demo_mode: false } });
    consumeMock.mockReturnValue(null);
    reasonOfMock.mockReturnValue(null);
    challengeOfMock.mockReturnValue(null);
  });

  it('explains a policy-expired session from the stored reason and consumes it', async () => {
    consumeMock.mockReturnValue('session_idle_expired');
    renderLogin();

    const banner = await screen.findByTestId('session-end-banner');
    expect(banner).toHaveTextContent('auth.session_ended_idle');
    expect(consumeMock).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('auth.email_label')).toBeInTheDocument();
  });

  it('explains a max-age session end', async () => {
    consumeMock.mockReturnValue('session_max_age_expired');
    renderLogin();

    expect(await screen.findByTestId('session-end-banner')).toHaveTextContent(
      'auth.session_ended_max_age'
    );
  });

  it('explains an IP-allowlist session end', async () => {
    consumeMock.mockReturnValue('ip_not_allowed');
    renderLogin();

    expect(await screen.findByTestId('session-end-banner')).toHaveTextContent(
      'auth.session_ended_ip'
    );
  });

  it('shows no banner when the session ended silently', async () => {
    renderLogin();

    await screen.findByLabelText('auth.email_label');
    expect(screen.queryByTestId('session-end-banner')).not.toBeInTheDocument();
  });

  it('maps a login 403 ip_not_allowed to the network-specific error', async () => {
    const user = userEvent.setup();
    reasonOfMock.mockImplementation((err: unknown) =>
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ===
      'ip_not_allowed'
        ? 'ip_not_allowed'
        : null
    );
    apiPost.mockRejectedValue({
      response: { status: 403, data: { detail: 'ip_not_allowed' } },
    });
    renderLogin();

    await user.type(await screen.findByLabelText('auth.email_label'), 'admin@clinic.dev');
    await user.type(screen.getByLabelText('auth.password_label'), 'secret-password');
    await user.click(screen.getByRole('button', { name: 'auth.sign_in_button' }));

    await waitFor(() => {
      expect(screen.getByText('auth.error_ip_not_allowed')).toBeInTheDocument();
    });
  });

  it('still handles the MFA challenge path (policy roles + SYSTEM_ADMIN)', async () => {
    const user = userEvent.setup();
    apiPost.mockRejectedValue({
      response: {
        status: 401,
        data: { detail: 'mfa_required', mfa_token: 'tok', enrollment_needed: false },
      },
    });
    challengeOfMock.mockImplementation((err: unknown) => {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      return data?.detail === 'mfa_required'
        ? { detail: 'mfa_required', mfa_token: String(data.mfa_token), enrollment_needed: false }
        : null;
    });
    renderLogin();

    await user.type(await screen.findByLabelText('auth.email_label'), 'root@clinic.dev');
    await user.type(screen.getByLabelText('auth.password_label'), 'secret-password');
    await user.click(screen.getByRole('button', { name: 'auth.sign_in_button' }));

    expect(await screen.findByLabelText('Verification code')).toBeInTheDocument();
    expect(screen.getByText('Two-factor verification')).toBeInTheDocument();
  });

  it('runs forced enrollment when the policy challenge carries enrollment_needed', async () => {
    const user = userEvent.setup();
    apiPost.mockRejectedValue({
      response: {
        status: 401,
        data: { detail: 'mfa_required', mfa_token: 'tok', enrollment_needed: true },
      },
    });
    challengeOfMock.mockReturnValue({
      detail: 'mfa_required',
      mfa_token: 'tok',
      enrollment_needed: true,
    });
    vi.mocked(enrollForcedMfa).mockResolvedValue({
      secret: 'SECR',
      uri: 'otpauth://x',
      recovery_codes: ['1234-5678'],
    });
    renderLogin();

    await user.type(await screen.findByLabelText('auth.email_label'), 'root@clinic.dev');
    await user.type(screen.getByLabelText('auth.password_label'), 'secret-password');
    await user.click(screen.getByRole('button', { name: 'auth.sign_in_button' }));

    expect(await screen.findByText('Set up two-factor authentication')).toBeInTheDocument();
    expect(enrollForcedMfa).toHaveBeenCalledWith('tok');
  });
});
