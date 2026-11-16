import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
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
  default: { get: vi.fn(), post: vi.fn() },
  consumeSessionEndReason: vi.fn(),
  sessionEndReasonOf: vi.fn(),
  probeSession: vi.fn().mockResolvedValue(false),
}));

vi.mock('../../../services/mfaService', () => ({
  mfaChallengeOf: vi.fn(),
  verifyMfaChallenge: vi.fn(),
  enrollForcedMfa: vi.fn(),
}));

vi.mock('../../../services/passkeyService', () => ({
  loginWithPasskey: vi.fn(),
  passkeysSupported: vi.fn(() => true),
}));

vi.mock('../../../services/ssoService', () => ({
  checkSsoAvailability: vi.fn(),
  beginSso: vi.fn(),
  confirmSsoLink: vi.fn(),
  readSsoPopupOutcome: vi.fn(),
}));

const loginMock = vi.fn();
vi.mock('../../../store/slices/authSlice', () => ({
  useAuthStore: (selector?: (state: { login: () => void }) => unknown) => {
    const state = { login: loginMock };
    return selector ? selector(state) : state;
  },
}));

vi.mock('../../../store/slices/settingsSlice', () => ({
  useSettingsStore: (selector: (state: { theme: string }) => unknown) =>
    selector({ theme: 'light' }),
}));

import api, { probeSession } from '../../../api/axios';
import { mfaChallengeOf } from '../../../services/mfaService';
import { loginWithPasskey, passkeysSupported } from '../../../services/passkeyService';
import { beginSso, checkSsoAvailability } from '../../../services/ssoService';
import Login from '../Login';

const apiGet = api.get as unknown as Mock;
const challengeOf = mfaChallengeOf as unknown as Mock;
const checkSso = checkSsoAvailability as unknown as Mock;
const startSso = beginSso as unknown as Mock;
const passkeyLogin = loginWithPasskey as unknown as Mock;
const passkeySupported = passkeysSupported as unknown as Mock;
const probe = probeSession as unknown as Mock;

const popup = {
  closed: false,
  close: vi.fn(),
  location: { href: 'https://idp.example/authorize' },
  document: { body: { innerText: '' } },
};

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>,
  );
}

async function ready() {
  apiGet.mockResolvedValue({ data: { initialized: true, demo_mode: false } });
  return renderLogin();
}

describe('Login — SSO, passkey and MFA methods (program 12B/12C)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    passkeySupported.mockReturnValue(true);
    probe.mockResolvedValue(false);
    checkSso.mockResolvedValue(true);
    startSso.mockResolvedValue({
      authorization_url: 'https://idp.example/authorize',
      issuer: 'https://idp.example',
    });
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  });

  it('discovers SSO on email blur and opens the provider popup', async () => {
    const user = userEvent.setup();
    await ready();

    const email = await screen.findByLabelText('auth.email_label');
    await user.type(email, 'doctor@clinic.example');
    await user.tab();

    const ssoCard = await screen.findByTestId('sso-available');
    expect(ssoCard).toBeInTheDocument();
    expect(checkSso).toHaveBeenCalledWith('doctor@clinic.example');

    await user.click(screen.getByTestId('sso-sign-in'));
    await waitFor(() => expect(startSso).toHaveBeenCalledWith({ email: 'doctor@clinic.example' }));
    expect(window.open).toHaveBeenCalledWith(
      'https://idp.example/authorize',
      'ha_sso',
      expect.stringContaining('popup'),
    );
  });

  it('signs in with a passkey when the browser supports WebAuthn', async () => {
    const user = userEvent.setup();
    passkeyLogin.mockResolvedValue(undefined);
    await ready();

    await user.click(await screen.findByTestId('passkey-sign-in'));

    await waitFor(() => expect(passkeyLogin).toHaveBeenCalledWith(undefined));
    expect(loginMock).toHaveBeenCalled();
  });

  it('shows only the passkey option for a webauthn-only MFA challenge', async () => {
    apiGet.mockResolvedValue({ data: { initialized: true, demo_mode: false } });
    const challenge = {
      detail: 'mfa_required',
      mfa_token: 'tok',
      enrollment_needed: false,
      methods: ['webauthn'],
    };
    (api.post as unknown as Mock).mockRejectedValue({
      response: { status: 401, data: challenge },
    });
    challengeOf.mockReturnValue(challenge);
    (loginWithPasskey as unknown as Mock).mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderLogin();
    await user.type(await screen.findByLabelText('auth.email_label'), 'nurse@clinic.example');
    await user.type(screen.getByLabelText('auth.password_label'), 'secret');
    await user.click(screen.getByRole('button', { name: 'auth.sign_in_button' }));

    expect(await screen.findByTestId('mfa-passkey')).toBeInTheDocument();
    expect(screen.queryByLabelText('auth.mfa_code_label')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('mfa-passkey'));
    await waitFor(() => expect(loginWithPasskey).toHaveBeenCalledWith('nurse@clinic.example'));
  });

  it('shows the TOTP form for a totp-only challenge', async () => {
    const challenge = {
      detail: 'mfa_required',
      mfa_token: 'tok',
      enrollment_needed: false,
      methods: ['totp'],
    };
    (api.post as unknown as Mock).mockRejectedValue({
      response: { status: 401, data: challenge },
    });
    challengeOf.mockReturnValue(challenge);

    const user = userEvent.setup();
    renderLogin();
    await user.type(await screen.findByLabelText('auth.email_label'), 'nurse@clinic.example');
    await user.type(screen.getByLabelText('auth.password_label'), 'secret');
    await user.click(screen.getByRole('button', { name: 'auth.sign_in_button' }));

    expect(await screen.findByLabelText('Verification code')).toBeInTheDocument();
    expect(screen.queryByTestId('mfa-passkey')).not.toBeInTheDocument();
  });
});
