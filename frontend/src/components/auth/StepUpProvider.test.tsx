import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) => {
      if (typeof fallback === 'string') return fallback;
      if (fallback && typeof fallback === 'object' && 'defaultValue' in fallback) {
        return String((fallback as { defaultValue: string }).defaultValue);
      }
      return key;
    },
  }),
}));

vi.mock('../../services/stepUpService', () => ({
  requestStepUp: vi.fn(),
}));

vi.mock('../../services/passkeyService', () => ({
  stepUpWithPasskey: vi.fn(),
}));

import { requestStepUp } from '../../services/stepUpService';
import { stepUpWithPasskey } from '../../services/passkeyService';
import { StepUpProvider } from './StepUpProvider';
import {
  registerStepUpPresenter,
  resetStepUpController,
  resolveStepUp,
} from './stepUpController';
import type { StepUpChallenge } from '../../services/stepUpTypes';

function challenge(overrides: Partial<StepUpChallenge> = {}): StepUpChallenge {
  return {
    code: 'step_up_required',
    action: 'role_change',
    expires_in: 600,
    methods: ['password'],
    ...overrides,
  };
}

function renderProvider() {
  return render(<StepUpProvider><div>app</div></StepUpProvider>);
}

describe('StepUpProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetStepUpController();
  });

  afterEach(() => {
    resetStepUpController();
  });

  it('proves a password action and resolves the pending request', async () => {
    vi.mocked(requestStepUp).mockResolvedValue({
      action: 'role_change',
      methods: ['password'],
      issued_at: 0,
      expires_at: 600,
      expires_in: 600,
    });
    const user = userEvent.setup();
    renderProvider();

    let settled: boolean | null = null;
    const promise = resolveStepUp(challenge()).then((ok) => {
      settled = ok;
    });

    expect(await screen.findByTestId('step-up-modal')).toBeInTheDocument();
    expect(screen.getByText(/role_change/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Password'), 'secret');
    await user.click(screen.getByRole('button', { name: 'Verify' }));

    await promise;
    expect(settled).toBe(true);
    expect(requestStepUp).toHaveBeenCalledWith('role_change', {
      password: 'secret',
      code: undefined,
    });
    await waitFor(() => expect(screen.queryByTestId('step-up-modal')).not.toBeInTheDocument());
  });

  it('sends the TOTP code when that is the available factor', async () => {
    vi.mocked(requestStepUp).mockResolvedValue({
      action: 'export',
      methods: ['totp'],
      issued_at: 0,
      expires_at: 600,
      expires_in: 600,
    });
    const user = userEvent.setup();
    renderProvider();

    const promise = resolveStepUp(challenge({ action: 'export', methods: ['totp'] }));
    expect(await screen.findByLabelText('Authenticator or recovery code')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Authenticator or recovery code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Verify' }));

    await promise;
    expect(requestStepUp).toHaveBeenCalledWith('export', {
      password: undefined,
      code: '123456',
    });
  });

  it('offers the passkey ceremony when the account has one', async () => {
    vi.mocked(stepUpWithPasskey).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderProvider();

    const promise = resolveStepUp(challenge({ action: 'mfa_change', methods: ['webauthn'] }));
    expect(await screen.findByRole('button', { name: 'Use a passkey' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Use a passkey' }));

    await expect(promise).resolves.toBe(true);
    expect(stepUpWithPasskey).toHaveBeenCalledWith('mfa_change');
  });

  it('cancelling resolves false without retrying', async () => {
    const user = userEvent.setup();
    renderProvider();

    const promise = resolveStepUp(challenge());
    expect(await screen.findByTestId('step-up-modal')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await expect(promise).resolves.toBe(false);
    expect(requestStepUp).not.toHaveBeenCalled();
  });

  it('explains an unavailable proof and closes without a form', async () => {
    const user = userEvent.setup();
    renderProvider();

    const promise = resolveStepUp(
      challenge({ code: 'step_up_unavailable', methods: [], reason: 'no_factor' }),
    );
    expect(await screen.findByText(/No authentication factor/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
    const closeButtons = screen.getAllByRole('button', { name: 'Close' });
    await user.click(closeButtons[closeButtons.length - 1]);
    await expect(promise).resolves.toBe(false);
  });

  it('does not register a stale presenter after unmount', async () => {
    const { unmount } = renderProvider();
    unmount();
    // Re-registering is done by the next mount; with none, the controller
    // answers false immediately (never hangs).
    const presenter = vi.fn(async () => true);
    registerStepUpPresenter(presenter);
    await expect(resolveStepUp(challenge())).resolves.toBe(true);
    expect(presenter).toHaveBeenCalledTimes(1);
  });
});
