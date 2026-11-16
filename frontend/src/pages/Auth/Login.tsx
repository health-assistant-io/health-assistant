import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import api, {
  consumeSessionEndReason,
  probeSession,
  sessionEndReasonOf,
  type SessionEndReason,
} from '../../api/axios';
import AppVersion from '../../components/ui/AppVersion';
import { QRCodeSVG } from 'qrcode.react';
import { Copy, Check, Fingerprint, Loader2, ShieldCheck } from 'lucide-react';
import {
  mfaChallengeOf,
  verifyMfaChallenge,
  enrollForcedMfa,
  type MFAEnrollment,
} from '../../services/mfaService';
import { loginWithPasskey, passkeysSupported } from '../../services/passkeyService';
import {
  beginSso,
  checkSsoAvailability,
  confirmSsoLink,
  readSsoPopupOutcome,
  type SsoCallbackOutcome,
} from '../../services/ssoService';

import Setup from './Setup';

const SSO_POLL_INTERVAL_MS = 900;
const SSO_FLOW_TIMEOUT_MS = 5 * 60 * 1000;

function ssoErrorMessage(
  code: string | undefined,
  t: TFunction,
): string {
  switch (code) {
    case 'sso_unavailable':
      return t('auth.sso_unavailable', 'Single sign-on is not available for this address. Use your password.');
    case 'sso_provider_unavailable':
      return t('auth.sso_provider_unavailable', 'The identity provider could not be reached. Try again later.');
    case 'sso_client_secret_missing':
      return t('auth.sso_misconfigured', 'Single sign-on is not configured on this host. Contact your administrator.');
    case 'sso_link_required':
      return t('auth.sso_link_required', 'This email already belongs to a local account. Sign in with your password, then link SSO.');
    case 'ip_not_allowed':
      return t('auth.error_ip_not_allowed');
    default:
      return t('auth.sso_failed', 'Single sign-on failed. Try again or use your password.');
  }
}

/**
 * The login surface (identity-auth §12) — health's `AuthGate` login node.
 * Rendered by the shared gate machine while `anonymous`; everything the
 * gate needs to leave that state is composed HERE (plan 16 closeout):
 * the demo auto-login (§13), the first-run setup wizard for an
 * uninitialized instance, and the H5 MFA challenge/enrollment step all
 * live inside this node. A successful sign-in — by any of those paths —
 * calls `onSignedIn()`, which bumps the gate's `resetKey` so the boot
 * flow re-runs against the fresh cookie session.
 *
 * Sign-in paths (ADR 0054 Phase 6 / program 12B–12C):
 *   - password (+ the MFA challenge, with TOTP and/or passkey factors);
 *   - a passkey assertion (`/auth/passkeys/*`);
 *   - per-tenant OIDC SSO (`/auth/sso/*`) in a popup: the callback sets
 *     the cookie session or answers the typed MFA-challenge JSON.
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
  const [mfaMethods, setMfaMethods] = useState<string[]>([]);
  const [secretCopied, setSecretCopied] = useState(false);

  // Per-tenant SSO discovery (program 12B): one bit per email domain.
  const [ssoAvailable, setSsoAvailable] = useState(false);
  const [ssoChecking, setSsoChecking] = useState(false);
  const [ssoBusy, setSsoBusy] = useState(false);
  const [pendingLinkToken, setPendingLinkToken] = useState<string | null>(null);
  const ssoPollRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  // ADR 0054 Phase 3: a session that died on a policy window (idle /
  // max-age) or the IP allowlist redirects here with a typed reason —
  // explain it once (consume), never loop the user back.
  const [sessionEnd, setSessionEnd] = useState<SessionEndReason | null>(null);
  useEffect(() => {
    const reason = consumeSessionEndReason();
    if (reason) setSessionEnd(reason);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (ssoPollRef.current !== null) {
        window.clearInterval(ssoPollRef.current);
        ssoPollRef.current = null;
      }
    };
  }, []);

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

  const finishLogin = useCallback(() => {
    login();
    navigate('/dashboard', { replace: true });
    onSignedIn?.();
  }, [login, navigate, onSignedIn]);

  // D2 link-only SSO: the callback refuses to merge an existing local
  // account and hands back a one-time link intent. The user signs in with
  // the local factor, then this completes the link before entering the app.
  const completeLogin = useCallback(async () => {
    if (pendingLinkToken) {
      try {
        await confirmSsoLink(pendingLinkToken);
        setPendingLinkToken(null);
      } catch {
        setError(t('auth.sso_link_failed', 'Signed in, but the SSO link could not be completed. Retry from settings.'));
      }
    }
    finishLogin();
  }, [pendingLinkToken, finishLogin, t]);

  const backToPasswordStep = () => {
    setMfaToken(null);
    setMfaEnrollment(null);
    setMfaCode('');
    setMfaMethods([]);
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

  const discoverSso = async (): Promise<boolean> => {
    const trimmed = email.trim();
    if (!trimmed || !trimmed.includes('@')) {
      setSsoAvailable(false);
      return false;
    }
    setSsoChecking(true);
    try {
      const available = await checkSsoAvailability(trimmed);
      setSsoAvailable(available);
      return available;
    } catch {
      setSsoAvailable(false);
      return false;
    } finally {
      setSsoChecking(false);
    }
  };

  const handleSsoOutcome = useCallback(async (outcome: SsoCallbackOutcome) => {
    if (outcome.kind === 'mfa_required') {
      setMfaToken(outcome.mfaToken ?? null);
      setMfaMethods(outcome.methods ?? []);
      setMfaCode('');
      setMfaEnrollment(null);
      setError(null);
      if (outcome.enrollmentNeeded && outcome.mfaToken) {
        try {
          setMfaEnrollment(await enrollForcedMfa(outcome.mfaToken));
        } catch (enrollErr) {
          console.error('Forced MFA enrollment failed:', enrollErr);
          setError(t('auth.error_server_down'));
        }
      }
      return;
    }
    if (outcome.kind === 'link_required') {
      setPendingLinkToken(outcome.linkToken ?? null);
      setError(ssoErrorMessage('sso_link_required', t));
      return;
    }
    setError(
      outcome.code
        ? ssoErrorMessage(outcome.code, t)
        : outcome.message || t('auth.sso_failed', 'Single sign-on failed. Try again or use your password.'),
    );
  }, [t]);

  const pollSsoPopup = useCallback((popup: Window) => {
    if (ssoPollRef.current !== null) {
      window.clearInterval(ssoPollRef.current);
    }
    const startedAt = Date.now();
    const stop = () => {
      if (ssoPollRef.current !== null) {
        window.clearInterval(ssoPollRef.current);
        ssoPollRef.current = null;
      }
    };
    ssoPollRef.current = window.setInterval(async () => {
      if (!mountedRef.current) {
        stop();
        return;
      }
      const outcome = readSsoPopupOutcome(popup);
      if (outcome) {
        stop();
        try { popup.close(); } catch { /* already closed */ }
        setSsoBusy(false);
        void handleSsoOutcome(outcome);
        return;
      }
      if (await probeSession()) {
        stop();
        try { popup.close(); } catch { /* already closed */ }
        setSsoBusy(false);
        finishLogin();
        return;
      }
      if (popup.closed) {
        stop();
        setSsoBusy(false);
        if (await probeSession()) {
          finishLogin();
        } else {
          setError(t('auth.sso_cancelled', 'Sign-in was cancelled. Try again.'));
        }
        return;
      }
      if (Date.now() - startedAt > SSO_FLOW_TIMEOUT_MS) {
        stop();
        try { popup.close(); } catch { /* already closed */ }
        setSsoBusy(false);
        setError(t('auth.sso_timeout', 'The single sign-on window timed out. Try again.'));
      }
    }, SSO_POLL_INTERVAL_MS);
  }, [finishLogin, handleSsoOutcome, t]);

  const startSso = async () => {
    const trimmed = email.trim();
    if (!trimmed) {
      setError(t('auth.sso_email_required', 'Enter your email address first.'));
      return;
    }
    setSsoBusy(true);
    setError(null);
    try {
      const target = await beginSso({ email: trimmed });
      const popup = window.open(
        target.authorization_url,
        'ha_sso',
        'popup,width=520,height=680',
      );
      if (!popup) {
        setSsoBusy(false);
        setError(t('auth.sso_popup_blocked', 'Allow pop-ups for this site to sign in with SSO.'));
        return;
      }
      pollSsoPopup(popup);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      const code =
        detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string'
          ? (detail as { code: string }).code
          : undefined;
      setSsoBusy(false);
      setError(ssoErrorMessage(code, t));
    }
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
        await completeLogin();
      } else {
        setError(t('auth.error_invalid_credentials'));
      }
    } catch (err) {
      const errorObj = err as Record<string, any>;

      // H5: the MFA challenge is a 401 with a machine-readable body —
      // distinct from invalid credentials. The axios interceptor lets
      // auth/* 401s through untouched (no refresh-retry).
      const challenge = mfaChallengeOf(errorObj);
      if (challenge) {
        setMfaToken(challenge.mfa_token);
        setMfaMethods(challenge.methods ?? []);
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
      } else if (errorObj?.response?.status === 403 && sessionEndReasonOf(errorObj)) {
        // The IP allowlist denied this sign-in (ADR 0054 Phase 3) —
        // a distinct, non-credential message.
        setError(t('auth.error_ip_not_allowed'));
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
      await completeLogin();
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

  const handlePasskeyLogin = async () => {
    setLoading(true);
    setError(null);
    try {
      await loginWithPasskey(email.trim() || undefined);
      await completeLogin();
    } catch (err) {
      const errorObj = err as Record<string, any>;
      const status = errorObj?.response?.status;
      const detail = errorObj?.response?.data?.detail;
      if (status === 423) {
        setError(t('auth.error_locked', 'Account locked; try again later.'));
      } else if (status === 403 && detail === 'ip_not_allowed') {
        setError(t('auth.error_ip_not_allowed'));
      } else if (status === 403 && detail === 'webauthn_clone_detected') {
        setError(t('auth.passkey_clone_detected', 'This passkey was disabled after a security anomaly. Use another factor.'));
      } else if (errorObj?.name === 'NotAllowedError' || status === 401) {
        setError(t('auth.passkey_failed', 'The passkey could not be verified. Try again or use your password.'));
      } else {
        setError(t('auth.passkey_failed', 'The passkey could not be verified. Try again or use your password.'));
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

  const sessionEndBanner = sessionEnd && (
    <div
      className="mb-6 p-4 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800/50 rounded-lg flex items-start gap-3 text-amber-800 dark:text-amber-400 text-sm animate-in fade-in slide-in-from-top-2 duration-200"
      data-testid="session-end-banner"
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5 shrink-0 mt-0.5">
        <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
      </svg>
      <div>
        {sessionEnd === 'session_idle_expired'
          ? t('auth.session_ended_idle')
          : sessionEnd === 'session_max_age_expired'
            ? t('auth.session_ended_max_age')
            : t('auth.session_ended_ip')}
      </div>
    </div>
  );

  const errorBanner = error && (
    <div className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-lg flex items-start gap-3 text-red-700 dark:text-red-400 text-sm animate-in fade-in slide-in-from-top-2 duration-200">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5 shrink-0 mt-0.5">
        <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
      </svg>
      <div>{error}</div>
    </div>
  );

  // ---- MFA challenge step (H5 + 12C methods) ------------------------------
  if (mfaToken) {
    const needsTotp = mfaMethods.length === 0 || mfaMethods.includes('totp');
    const allowsPasskey = mfaMethods.includes('webauthn');
    const totpOnlyUnavailable = !needsTotp && !allowsPasskey;
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
                : allowsPasskey && !needsTotp
                  ? t('auth.mfa_passkey_hint', 'Confirm with your passkey to finish signing in.')
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

          {needsTotp ? (
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
            </form>
          ) : totpOnlyUnavailable ? (
            <p className="text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/40 rounded-xl px-3 py-2">
              {t('auth.mfa_no_factor', 'No usable second factor is available. Contact your administrator.')}
            </p>
          ) : null}

          {allowsPasskey && (
            <button
              type="button"
              onClick={handlePasskeyLogin}
              disabled={loading}
              className="w-full mt-3 inline-flex items-center justify-center gap-2 px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg text-sm font-bold text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border disabled:opacity-50"
              data-testid="mfa-passkey"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Fingerprint className="w-4 h-4" />}
              {t('auth.mfa_use_passkey', 'Use a passkey instead')}
            </button>
          )}

          <div className="text-center mt-4">
            <button
              type="button"
              onClick={backToPasswordStep}
              className="text-sm text-gray-500 dark:text-dark-muted hover:text-blue-600"
            >
              ← {t('auth.mfa_back', 'Back')}
            </button>
          </div>
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

        {sessionEndBanner}

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
                setSsoAvailable(false);
                setError(null);
              }}
              onBlur={discoverSso}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="you@example.com"
            />
            {ssoChecking && (
              <p className="text-xs text-gray-400 mt-1 flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" />
                {t('auth.sso_checking', 'Checking single sign-on…')}
              </p>
            )}
          </div>

          {ssoAvailable && (
            <div className="space-y-4" data-testid="sso-available">
              <button
                type="button"
                onClick={startSso}
                disabled={ssoBusy || loading}
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 border border-blue-200 dark:border-blue-900/40 text-blue-700 dark:text-blue-400 bg-blue-50/60 dark:bg-blue-900/10 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/20 font-bold text-sm disabled:opacity-50"
                data-testid="sso-sign-in"
              >
                {ssoBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
                {t('auth.sso_sign_in', 'Sign in with single sign-on')}
              </button>
              <div className="flex items-center gap-3 text-[11px] uppercase tracking-widest text-gray-400">
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border" />
                {t('auth.or_password', 'or use your password')}
                <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border" />
              </div>
            </div>
          )}

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
            disabled={loading || ssoBusy}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? t('auth.signing_in') : t('auth.sign_in_button')}
          </button>

          {passkeysSupported() && (
            <button
              type="button"
              onClick={handlePasskeyLogin}
              disabled={loading || ssoBusy}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg text-sm font-bold text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border disabled:opacity-50"
              data-testid="passkey-sign-in"
            >
              <Fingerprint className="w-4 h-4" />
              {t('auth.passkey_sign_in', 'Sign in with a passkey')}
            </button>
          )}

          <div className="text-center">
            <p className="text-sm text-gray-500 dark:text-dark-muted">
              {t('auth.no_account', 'Need an account?')}
              <br />
              <span className="text-xs">
                {t('auth.invite_only_hint', 'Ask your administrator for an invite, or set up a new install.')}
              </span>
            </p>
            <button
              type="button"
              onClick={async () => {
                if (!email.trim()) {
                  setError(t('auth.sso_email_required', 'Enter your email address first.'));
                  return;
                }
                const available = await discoverSso();
                if (available) {
                  await startSso();
                } else {
                  setError(t('auth.sso_unavailable', 'Single sign-on is not available for this address. Use your password.'));
                }
              }}
              disabled={ssoBusy || ssoChecking}
              className="mt-2 text-xs font-bold text-blue-600 hover:text-blue-700 disabled:opacity-50"
              data-testid="sso-link"
            >
              {t('auth.sso_link', 'Sign in with single sign-on instead')}
            </button>
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
