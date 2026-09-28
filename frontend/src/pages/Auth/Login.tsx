import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import api from '../../api/axios';
import AppVersion from '../../components/ui/AppVersion';
import { QRCodeSVG } from 'qrcode.react';
import { Copy, Check } from 'lucide-react';
import {
  mfaChallengeOf,
  verifyMfaChallenge,
  enrollForcedMfa,
  type MFAEnrollment,
} from '../../services/mfaService';

import Setup from './Setup';

/**
 * The login surface (identity-auth §12) — health's `AuthGate` login node.
 * Rendered by the shared gate machine while `anonymous`; everything the
 * gate needs to leave that state is composed HERE (plan 16 closeout):
 * the demo auto-login (§13), the first-run setup wizard for an
 * uninitialized instance, and the H5 MFA challenge/enrollment step all
 * live inside this node. A successful sign-in — by any of those paths —
 * calls `onSignedIn()`, which bumps the gate's `resetKey` so the boot
 * flow re-runs against the fresh cookie session.
 */
function Login({ onSignedIn }: { onSignedIn?: () => void }) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { login } = useAuthStore();
  const theme = useSettingsStore(state => state.theme);
  const [needsSetup, setNeedsSetup] = useState(false);
  // Stable identity for the setup node's "instance turned initialized"
  // flip-back (Setup's probe effect re-runs on it).
  const backToLoginForm = useCallback(() => setNeedsSetup(false), []);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // H5 MFA challenge state: the login 401 carries a short-lived
  // mfa_token; enrollment_needed means an admin forced MFA and the
  // authenticator must be enrolled before the challenge can pass.
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [mfaEnrollment, setMfaEnrollment] = useState<MFAEnrollment | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [secretCopied, setSecretCopied] = useState(false);

  // Probe first-run status on mount (the shared gate already settled the
  // session question — boot ran before this node rendered). If the system
  // is uninitialized, the setup wizard renders inside this login node
  // instead of the form. If DEMO_MODE is on, skip the form entirely and
  // auto-call demo-login. §10: there is no local token — the
  // server-verified cookie session is the credential; demo-login sets the
  // cookie triple and the body tokens are ignored by this client.
  useEffect(() => {
    const init = async () => {
      try {
        const res = await api.get('/auth/setup-status');
        if (res.data && res.data.demo_mode) {
          // Demo mode — auto-login as the pre-seeded demo user (no creds).
          try {
            const demoRes = await api.post('/auth/demo-login');
            if (demoRes.status < 400) {
              login();
              navigate('/dashboard', { replace: true });
              onSignedIn?.();
              return;
            }
          } catch (err) {
            console.error('Demo login failed:', err);
            // Fall through to the login form as a safety net.
          }
        }
        if (res.data && !res.data.initialized) {
          // First-run instance: the setup wizard replaces the form inside
          // the login node (no anonymous routes anymore — the gate owns
          // the machine and this node owns the wizard).
          setNeedsSetup(true);
          setChecking(false);
          return;
        }
      } catch {
        // Status endpoint unreachable — fall through to the login form.
      }
      setChecking(false);
    };

    init();
  }, [navigate, login, onSignedIn]);

  const backToPasswordStep = () => {
    setMfaToken(null);
    setMfaEnrollment(null);
    setMfaCode('');
    setError(null);
  };

  const copySecret = async () => {
    if (!mfaEnrollment) return;
    try {
      await navigator.clipboard.writeText(mfaEnrollment.secret);
    } catch { /* insecure context — the text stays selectable */ }
    setSecretCopied(true);
    setTimeout(() => setSecretCopied(false), 1500);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      // Use FormData for OAuth2 password grant
      const formData = new FormData();
      formData.append('username', email);
      formData.append('password', password);
      formData.append('grant_type', 'password');

      const response = await api.post('/auth/login', formData, {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
      });

      if (response.status < 400) {
        // §10: the backend set the cookie triple; the body tokens are for
        // §9 user clients and are deliberately ignored here.
        login();
        console.log('Login successful, redirecting to dashboard...');
        navigate('/dashboard', { replace: true });
        onSignedIn?.();
      } else {
        setError(t('auth.error_invalid_credentials'));
      }
    } catch (err) {
      const errorObj = err as Record<string, any>;
      console.error('Login failed:', errorObj);

      // H5: the MFA challenge is a 401 with a machine-readable body —
      // distinct from invalid credentials. The axios interceptor lets
      // auth/* 401s through untouched (no refresh-retry).
      const challenge = mfaChallengeOf(errorObj);
      if (challenge) {
        setMfaToken(challenge.mfa_token);
        setMfaCode('');
        setError(null);
        if (challenge.enrollment_needed && !mfaEnrollment) {
          try {
            const enrollment = await enrollForcedMfa(challenge.mfa_token);
            setMfaEnrollment(enrollment);
          } catch (enrollErr) {
            console.error('Forced MFA enrollment failed:', enrollErr);
            setError(t('auth.error_server_down'));
          }
        }
        return;
      }

      if (errorObj?.response?.status === 401) {
        setError(t('auth.error_invalid_credentials'));
      } else if (errorObj?.response?.status === 422) {
        setError(t('auth.error_invalid_format'));
      } else if (errorObj?.response?.status === 423) {
        setError(t('auth.error_locked', 'Account locked; try again later.'));
      } else {
        setError(t('auth.error_server_down'));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleMfaSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!mfaToken) return;
    setLoading(true);
    setError(null);
    try {
      await verifyMfaChallenge(mfaToken, mfaCode);
      // The backend set the §10 cookie triple; the body tokens are for
      // §9 user clients.
      login();
      navigate('/dashboard', { replace: true });
      onSignedIn?.();
    } catch (err) {
      const errorObj = err as Record<string, any>;
      const status = errorObj?.response?.status;
      const detail: string = errorObj?.response?.data?.detail ?? '';
      if (status === 423) {
        setError(t('auth.error_locked', 'Account locked; try again later.'));
      } else if (status === 401 && detail.includes('expired')) {
        // Challenge expired/consumed — back to credentials for a fresh one.
        setError(t('auth.mfa_challenge_expired', 'The verification step expired. Please sign in again.'));
        backToPasswordStep();
      } else {
        setError(t('auth.mfa_invalid_code', 'Invalid verification code. Try again.'));
      }
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-dark-bg">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  // First-run instance: the setup wizard is composed inside the login node
  // (plan 16 closeout — the gate has no anonymous routes; this node decides
  // between the wizard and the form). `onAlreadyInitialized` flips back to
  // the form when the instance was initialized under us (status race /
  // second tab, or the 410 the setup submit answers).
  if (needsSetup) {
    return (
      <Setup
        embedded
        onSignedIn={onSignedIn}
        onAlreadyInitialized={backToLoginForm}
      />
    );
  }

  const errorBanner = error && (
    <div className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-lg flex items-start gap-3 text-red-700 dark:text-red-400 text-sm animate-in fade-in slide-in-from-top-2 duration-200">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5 shrink-0 mt-0.5">
        <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
      </svg>
      <div>{error}</div>
    </div>
  );

  // ---- MFA challenge step (H5) -------------------------------------------
  if (mfaToken) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-dark-bg">
        <div className="max-w-md w-full bg-white dark:bg-dark-surface rounded-lg shadow-md p-8">
          <div className="text-center mb-6">
            <img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-12 h-12 mx-auto mb-3" alt="Health Assistant Logo" />
            <h1 className="text-2xl font-bold text-blue-600">
              {mfaEnrollment
                ? t('auth.mfa_enroll_title', 'Set up two-factor authentication')
                : t('auth.mfa_title', 'Two-factor verification')}
            </h1>
            <p className="text-gray-600 dark:text-dark-muted mt-2 text-sm">
              {mfaEnrollment
                ? t('auth.mfa_enroll_hint', 'Your organization requires MFA. Scan the QR with an authenticator app (e.g. Google Authenticator, Aegis), then enter the 6-digit code.')
                : t('auth.mfa_hint', 'Enter the 6-digit code from your authenticator app, or a recovery code.')}
            </p>
          </div>

          {errorBanner}

          {mfaEnrollment && (
            <div className="mb-6 space-y-3">
              <div className="flex justify-center">
                <div className="p-3 bg-white rounded-2xl border border-gray-100 dark:border-dark-border">
                  <QRCodeSVG value={mfaEnrollment.uri} size={160} level="M" />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-xs font-mono break-all dark:text-dark-text">
                  {mfaEnrollment.secret}
                </code>
                <button
                  type="button"
                  onClick={copySecret}
                  className="p-2 text-gray-400 hover:text-blue-600 rounded-lg transition-colors"
                  title={t('mfa.copy', 'Copy')}
                >
                  {secretCopied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
              <div className="p-3 rounded-xl border border-amber-200 dark:border-amber-900/40 bg-amber-50/60 dark:bg-amber-900/10 space-y-2">
                <p className="text-xs font-bold text-amber-800 dark:text-amber-300">
                  {t('mfa.recovery_title', 'Recovery codes')}
                </p>
                <p className="text-[11px] text-amber-700 dark:text-amber-400">
                  {t('mfa.recovery_help', 'Single-use codes for when you lose your authenticator. Shown only once — store them now.')}
                </p>
                <div className="grid grid-cols-4 gap-1.5">
                  {mfaEnrollment.recovery_codes.map(code => (
                    <code key={code} className="px-1.5 py-1 bg-white dark:bg-dark-surface border border-amber-200 dark:border-amber-900/40 rounded text-[10px] font-mono font-bold text-amber-900 dark:text-amber-200 text-center">
                      {code}
                    </code>
                  ))}
                </div>
              </div>
            </div>
          )}

          <form onSubmit={handleMfaSubmit} className="space-y-6">
            <div>
              <label htmlFor="mfa-code" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                {t('auth.mfa_code_label', 'Verification code')}
              </label>
              <input
                id="mfa-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                autoFocus
                value={mfaCode}
                onChange={(e) => { setMfaCode(e.target.value); setError(null); }}
                className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text text-center text-lg tracking-[0.4em] font-mono"
                placeholder="••••••"
                maxLength={11}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? t('auth.signing_in') : t('auth.mfa_verify_button', 'Verify & sign in')}
            </button>

            <div className="text-center">
              <button
                type="button"
                onClick={backToPasswordStep}
                className="text-sm text-gray-500 dark:text-dark-muted hover:text-blue-600"
              >
                ← {t('auth.mfa_back', 'Back')}
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-dark-bg">
      <div className="max-w-md w-full bg-white dark:bg-dark-surface rounded-lg shadow-md p-8">
        <div className="text-center mb-8">
          <img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-16 h-16 mx-auto mb-4" alt="Health Assistant Logo" />
          <h1 className="text-3xl font-bold text-blue-600">Health Assistant</h1>
          <p className="text-gray-600 dark:text-dark-muted mt-2">
            {t('auth.sign_in_title')}
          </p>
        </div>

        {errorBanner}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label
              htmlFor="email"
              className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2"
            >
              {t('auth.email_label')}
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setError(null);
              }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2"
            >
              {t('auth.password_label')}
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setError(null);
              }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="••••••••"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? t('auth.signing_in') : t('auth.sign_in_button')}
          </button>

          <div className="text-center">
            <p className="text-sm text-gray-500 dark:text-dark-muted">
              {t('auth.no_account', 'Need an account?')}
              <br />
              <span className="text-xs">
                {t('auth.invite_only_hint', 'Ask your administrator for an invite, or set up a new install.')}
              </span>
            </p>
          </div>

          <div className="text-center border-t border-gray-100 dark:border-white/5 pt-4">
            <AppVersion />
          </div>
        </form>
      </div>
    </div>
  );
}

export default Login;
