import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { KeyRound, Loader2, ShieldAlert } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { requestStepUp } from '../../services/stepUpService';
import { stepUpChallengeOf, type StepUpChallenge } from '../../services/stepUpTypes';
import { stepUpWithPasskey } from '../../services/passkeyService';
import { registerStepUpPresenter } from './stepUpController';

const ACTION_LABEL_KEYS: Record<string, string> = {
  role_change: 'step_up.action_role_change',
  export: 'step_up.action_export',
  break_glass: 'step_up.action_break_glass',
  grant_admin: 'step_up.action_grant_admin',
  mfa_change: 'step_up.action_mfa_change',
};

function actionLabel(t: TFunction, action: string): string {
  const key = ACTION_LABEL_KEYS[action];
  return key ? t(key, action) : action;
}

/**
 * Global step-up prompt (program 12C1). The axios interceptor calls the
 * registered presenter when a gated request answers `403 step_up_required`;
 * a successful proof resolves `true` and the request is replayed once.
 *
 * The modal offers exactly the factors the backend named: password and/or
 * TOTP code, and a passkey assertion when the account has one. A proof is
 * a ten-minute window bound to the sign-in family and the action.
 */
export function StepUpProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [challenge, setChallenge] = useState<StepUpChallenge | null>(null);
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  useEffect(() => {
    registerStepUpPresenter(
      (next) =>
        new Promise<boolean>((resolve) => {
          resolver.current = resolve;
          setChallenge(next);
          setPassword('');
          setCode('');
          setError(null);
          setBusy(false);
        }),
    );
    return () => registerStepUpPresenter(null);
  }, []);

  const settle = useCallback((ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setChallenge(null);
    setPassword('');
    setCode('');
    setError(null);
    setBusy(false);
  }, []);

  if (!challenge) return <>{children}</>;

  const methods = challenge.methods;
  const needsPassword = methods.includes('password') || methods.length === 0;
  const needsCode = methods.includes('totp');
  const canPasskey = methods.includes('webauthn');
  const hasLocalFactor = needsPassword || needsCode;
  const unavailable = challenge.code === 'step_up_unavailable';

  const unavailableMessage = (() => {
    if (!unavailable) return null;
    if (challenge.reason === 'no_factor') return t('step_up.unavailable_no_factor', 'No authentication factor is available on this account. Ask your administrator for help.');
    if (challenge.reason === 'no_session_family') return t('step_up.unavailable_no_session_family', 'This sign-in cannot be re-authenticated. Sign in again and retry.');
    if (challenge.reason === 'account_unavailable') return t('step_up.unavailable_account_unavailable', 'The account is no longer available.');
    return t('step_up.unavailable', 'Additional verification is not available for this action.');
  })();

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await requestStepUp(challenge.action, {
        password: password || undefined,
        code: code || undefined,
      });
      settle(true);
    } catch (err) {
      const next = stepUpChallengeOf(err);
      if (next?.code === 'step_up_required' && next.methods.length > 0) {
        setChallenge((current) => (current ? { ...current, methods: next.methods } : current));
        setError(t('step_up.more_factors', 'Additional verification is required — provide the highlighted factor.'));
      } else if ((err as { response?: { status?: number } })?.response?.status === 401) {
        setError(t('step_up.invalid', 'That password or code is incorrect. Try again.'));
      } else {
        setError(t('step_up.failed', 'Verification failed. Try again.'));
      }
    } finally {
      setBusy(false);
    }
  };

  const usePasskey = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await stepUpWithPasskey(challenge.action);
      settle(true);
    } catch (err) {
      const next = stepUpChallengeOf(err);
      if (next?.code === 'step_up_unavailable') {
        setError(unavailableMessageFor(t, next.reason));
      } else {
        setError(t('step_up.passkey_failed', 'The passkey could not be verified. Try again.'));
      }
      setBusy(false);
    }
  };

  const methodsKnown = methods.length > 0;
  const submitDisabled = busy
    ? true
    : methodsKnown
      ? (needsPassword && !password) || (needsCode && !code)
      : !password && !code;

  return (
    <>
      {children}
      <Modal
        open
        onOpenChange={(open) => {
          if (!open) settle(false);
        }}
        title={t('step_up.title', 'Confirm your identity')}
        className="max-w-md"
      >
        <div className="p-6 space-y-5" data-testid="step-up-modal">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded-xl bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400">
              <ShieldAlert className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold text-gray-900 dark:text-dark-text">
                {t('step_up.heading', 'Extra verification required')}
              </p>
              <p className="text-sm text-gray-500 dark:text-dark-muted mt-1">
                {t('step_up.description', {
                  action: actionLabel(t, challenge.action),
                  defaultValue: `Re-authenticate to continue: ${actionLabel(t, challenge.action)}.`,
                })}
              </p>
            </div>
          </div>

          {unavailable ? (
            <p className="text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/40 rounded-xl px-3 py-2">
              {unavailableMessage}
            </p>
          ) : (
            <form onSubmit={submit} className="space-y-4">
              {needsPassword && (
                <div>
                  <label htmlFor="step-up-password" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                    {t('step_up.password_label', 'Password')}
                  </label>
                  <input
                    id="step-up-password"
                    type="password"
                    autoComplete="current-password"
                    autoFocus
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value);
                      setError(null);
                    }}
                    className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
                    placeholder="••••••••"
                  />
                </div>
              )}

              {needsCode && (
                <div>
                  <label htmlFor="step-up-code" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                    {t('step_up.code_label', 'Authenticator or recovery code')}
                  </label>
                  <input
                    id="step-up-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={code}
                    onChange={(e) => {
                      setCode(e.target.value);
                      setError(null);
                    }}
                    className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text text-center tracking-[0.3em] font-mono"
                    placeholder="••••••"
                    maxLength={11}
                  />
                </div>
              )}

              {error && (
                <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                  {error}
                </p>
              )}

              <div className="flex flex-col gap-2">
                {hasLocalFactor && (
                  <button
                    type="submit"
                    disabled={submitDisabled}
                    className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-bold text-sm disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
                    {t('step_up.submit', 'Verify')}
                  </button>
                )}
                {!hasLocalFactor && !canPasskey && (
                  <p className="text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/40 rounded-xl px-3 py-2">
                    {t('step_up.unavailable_no_factor', 'No authentication factor is available on this account. Ask your administrator for help.')}
                  </p>
                )}
                {canPasskey && (
                  <button
                    type="button"
                    onClick={usePasskey}
                    disabled={busy}
                    className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg text-sm font-bold text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border disabled:opacity-50"
                  >
                    {t('step_up.passkey_button', 'Use a passkey')}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => settle(false)}
                  className="w-full px-4 py-2 text-sm text-gray-500 dark:text-dark-muted hover:text-gray-700 dark:hover:text-dark-text"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
              </div>
            </form>
          )}

          {unavailable && (
            <button
              type="button"
              onClick={() => settle(false)}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg text-sm font-bold text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border"
            >
              {t('common.close', 'Close')}
            </button>
          )}
        </div>
      </Modal>
    </>
  );
}

function unavailableMessageFor(
  t: TFunction,
  reason: string | undefined,
): string {
  if (reason === 'no_factor') return t('step_up.unavailable_no_factor', 'No authentication factor is available on this account. Ask your administrator for help.');
  if (reason === 'no_session_family') return t('step_up.unavailable_no_session_family', 'This sign-in cannot be re-authenticated. Sign in again and retry.');
  if (reason === 'account_unavailable') return t('step_up.unavailable_account_unavailable', 'The account is no longer available.');
  return t('step_up.unavailable', 'Additional verification is not available for this action.');
}
