import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Lock, Monitor, Shield, ShieldCheck, Smartphone, Loader2 } from 'lucide-react';
import { ErrorBanner } from '@neuronection/assistant-ui';
import { AccountSessionList, type AccountSessionRow } from '@neuronection/assistant-ui/account-session-list';
import { PageHeader } from '../../components/ui/PageHeader';
import { FormModal } from '../../components/ui/FormModal';
import { MFAEnrollmentPanel } from '../../components/auth/MFAEnrollmentPanel';
import { PasskeyCard } from '../../components/settings/PasskeyCard';
import { ApiTokensCard } from '../../components/settings/ApiTokensCard';
import { MyElevationsCard } from '../../components/settings/MyElevationsCard';
import { useAuthStore } from '../../store/slices/authSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import {
  changeMyPassword,
  listMySessions,
  revokeMySession,
  revokeOtherSessions,
  type UserSession,
} from '../../services/sessionService';
import {
  getMyMfa,
  enrollMyMfa,
  confirmMyMfa,
  disableMyMfa,
  type MFAStatus,
  type MFAEnrollment,
} from '../../services/mfaService';

import { formatTimestamp } from '../../lib/dateFormats';
function formatSessionDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : formatTimestamp(date);
}

function Security() {
  const { t } = useTranslation();
  const logout = useAuthStore((state) => state.logout);
  const showConfirmation = useUIStore((s) => s.showConfirmation);

  const [status, setStatus] = useState<MFAStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [enrollment, setEnrollment] = useState<MFAEnrollment | null>(null);
  const [confirmCode, setConfirmCode] = useState('');
  const [disablePassword, setDisablePassword] = useState('');
  const [showDisable, setShowDisable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<UserSession[] | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [revokingOthers, setRevokingOthers] = useState(false);

  const [showPasswordModal, setShowPasswordModal] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [changingPassword, setChangingPassword] = useState(false);

  const passwordMismatch = confirmPassword.length > 0 && newPassword !== confirmPassword;

  const refresh = useCallback(async () => {
    try {
      setStatus(await getMyMfa());
    } catch (err) {
      console.error('Failed to load MFA status:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await listMySessions());
    } catch (err) {
      console.error('Failed to load sessions:', err);
      setSessions([]);
    }
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  const revokeSession = async (session: UserSession) => {
    setRevoking(session.id);
    try {
      await revokeMySession(session.id);
      if (session.current) {
        await logout();
        return;
      }
      toast.success(t('settings.sessions_revoked', 'Session revoked'));
      await loadSessions();
    } catch (err) {
      console.error('Failed to revoke session:', err);
      toast.error(t('settings.sessions_revoke_failed', 'Could not revoke this session.'));
    } finally {
      setRevoking(null);
    }
  };

  const revokeOthers = () => {
    showConfirmation({
      title: t('settings.sessions_revoke_others_title', 'Sign out other devices'),
      message: t('settings.sessions_revoke_others_message', 'Every other device is signed out; this one stays signed in.'),
      confirmLabel: t('settings.sessions_revoke_others', 'Sign out others'),
      cancelLabel: t('common.cancel', 'Cancel'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        setRevokingOthers(true);
        try {
          const count = await revokeOtherSessions();
          toast.success(
            t('settings.sessions_revoke_others_done', 'Signed out other devices.', { count }),
          );
          await loadSessions();
        } catch (err) {
          console.error('Failed to revoke other sessions:', err);
          toast.error(t('settings.sessions_revoke_failed', 'Could not revoke this session.'));
        } finally {
          setRevokingOthers(false);
        }
      },
    });
  };

  const sessionRows: AccountSessionRow[] = (sessions ?? []).map((session) => {
    const details = [session.ip || '—'];
    if (session.user_agent) details.push(session.user_agent);
    details.push(`${t('settings.sessions_signed_in', 'Signed in')}: ${formatSessionDate(session.created_at)}`);
    details.push(`${t('settings.sessions_last_seen', 'Last seen')}: ${formatSessionDate(session.last_seen_at)}`);
    details.push(`${t('settings.sessions_expires', 'Expires')}: ${formatSessionDate(session.expires_at)}`);
    // A row without a client label still needs a stable, unique name: the
    // library's revoke action carries the label, so two label-less sessions
    // would otherwise share one accessible name. The short session id keeps
    // them distinct (ip alone collides behind a shared NAT).
    const label = session.client_label
      || `${session.ip || t('settings.sessions_unknown_device', 'Unknown device')} · ${session.id.slice(0, 8)}`;
    return {
      id: session.id,
      current: session.current,
      label,
      details,
    };
  });

  const revokeSessionById = (id: string) => {
    const session = sessions?.find((candidate) => candidate.id === id);
    if (session) void revokeSession(session);
  };

  const changePassword = async () => {
    if (!currentPassword || !newPassword || newPassword !== confirmPassword) return;
    setChangingPassword(true);
    setPasswordError(null);
    try {
      await changeMyPassword(currentPassword, newPassword);
      setShowPasswordModal(false);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      toast.success(
        t('settings.change_password_success', 'Password updated — please sign in again.'),
      );
      // The backend revokes every token family on a password change (§12),
      // so the current session is already invalid. Clear local state and
      // return to the login screen; the logout POST may 401 now, which is
      // expected and must not surface as a password-change failure.
      await logout().catch(() => {});
    } catch (err) {
      console.error('Password change failed:', err);
      const resp = (err as Record<string, unknown>)?.response as
        | { status?: number; data?: { detail?: unknown } }
        | undefined;
      if (resp?.status === 400) {
        setPasswordError(
          typeof resp.data?.detail === 'string'
            ? resp.data.detail
            : t('settings.change_password_wrong_current', 'Current password is incorrect.'),
        );
      } else if (resp?.status === 422) {
        setPasswordError(
          typeof resp.data?.detail === 'string'
            ? resp.data.detail
            : t('settings.change_password_policy', 'The new password does not meet the policy (at least 10 characters).'),
        );
      } else if (resp?.status === 429) {
        setPasswordError(t('settings.change_password_rate_limited', 'Too many attempts — please wait a moment and try again.'));
      } else {
        setPasswordError(t('settings.change_password_failed', 'Could not change the password. Please try again.'));
      }
    } finally {
      setChangingPassword(false);
    }
  };

  const startEnrollment = async () => {
    setBusy(true);
    setError(null);
    try {
      setEnrollment(await enrollMyMfa());
      setConfirmCode('');
    } catch (err) {
      console.error('MFA enrollment failed:', err);
      setError(t('mfa.enroll_failed', 'Could not start enrollment. Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const confirmEnrollment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!enrollment) return;
    setBusy(true);
    setError(null);
    try {
      await confirmMyMfa(confirmCode);
      setEnrollment(null);
      setConfirmCode('');
      await refresh();
      toast.success(t('mfa.enabled_toast', 'Two-factor authentication enabled'));
    } catch (err) {
      console.error('MFA confirm failed:', err);
      setError(t('mfa.invalid_code', 'Invalid verification code — MFA was not activated.'));
    } finally {
      setBusy(false);
    }
  };

  const disable = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await disableMyMfa(disablePassword);
      setShowDisable(false);
      setDisablePassword('');
      setEnrollment(null);
      await refresh();
      toast.success(t('mfa.disabled_toast', 'Two-factor authentication disabled'));
    } catch (err) {
      const resp = (err as Record<string, any>)?.response;
      if (resp?.status === 403) {
        setError(resp?.data?.detail ?? t('mfa.enforced_cannot_disable', 'MFA is required by policy.'));
      } else {
        setError(t('mfa.invalid_password', 'Invalid password.'));
      }
    } finally {
      setBusy(false);
    }
  };

  const mfaCard = (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          {status?.enabled ? (
            <ShieldCheck className="w-5 h-5 text-green-600" />
          ) : (
            <Smartphone className="w-4 h-4 text-gray-400" />
          )}
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-dark-text">
              {t('settings.mfa_title', 'Two-factor authentication (TOTP)')}
            </p>
            <p className="text-sm text-gray-500 dark:text-dark-muted">
              {status?.totp_enabled
                ? t('settings.mfa_enabled_desc', 'Your account requires a 6-digit code from your authenticator at sign-in.')
                : status?.passkey_enabled
                  ? t('settings.mfa_enabled_passkey_desc', 'Your account signs in with a passkey; add TOTP as an additional factor if you wish.')
                  : t('settings.mfa_desc', 'Add a second factor with any authenticator app (Google Authenticator, Aegis, 1Password…).')}
            </p>
            {status && status.passkey_count > 0 && (
              <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">
                {t('settings.mfa_passkey_count', {
                  count: status.passkey_count,
                  defaultValue: `${status.passkey_count} passkey(s) registered as a factor.`,
                })}
              </p>
            )}
          </div>
        </div>
        {status && !status.totp_enabled && !enrollment && (
          <button
            onClick={startEnrollment}
            disabled={busy}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-bold disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : t('settings.mfa_enroll', 'Set up')}
          </button>
        )}
        {status?.totp_enabled && (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-black uppercase tracking-wide bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400">
            {t('settings.mfa_active', 'Active')}
          </span>
        )}
      </div>

      {status?.enforced && (
        <p className="text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/40 rounded-xl px-3 py-2">
          {t('settings.mfa_enforced_hint', 'Your administrator requires MFA on this account; it cannot be disabled here.')}
        </p>
      )}

      {status?.totp_enabled && (
        <p className="text-xs text-gray-500 dark:text-dark-muted" data-testid="recovery-codes-note">
          {t('settings.recovery_codes_note', 'Recovery codes are shown once when TOTP is enrolled. If you lost them, disable and re-enroll TOTP to receive a new set.')}
        </p>
      )}

      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {enrollment && (
        <form onSubmit={confirmEnrollment} className="space-y-5 border-t border-gray-100 dark:border-dark-border pt-5">
          <MFAEnrollmentPanel enrollment={enrollment} />
          <div>
            <label htmlFor="mfa-confirm-code" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              {t('mfa.confirm_label', 'Enter the current 6-digit code to activate')}
            </label>
            <div className="flex space-x-2">
              <input
                id="mfa-confirm-code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                value={confirmCode}
                onChange={(e) => { setConfirmCode(e.target.value); setError(null); }}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text text-center text-lg tracking-[0.3em] font-mono"
                placeholder="••••••"
                maxLength={11}
              />
              <button
                type="submit"
                disabled={busy}
                className="px-5 py-2 bg-brand-cyan text-white rounded-lg hover:bg-brand-cyan-hover transition-colors font-bold text-sm disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : t('settings.mfa_confirm', 'Activate')}
              </button>
            </div>
          </div>
        </form>
      )}

      {status?.totp_enabled && !showDisable && (
        <div className="border-t border-gray-100 dark:border-dark-border pt-4">
          <button
            onClick={() => status.enforced ? setError(t('settings.mfa_enforced_hint', 'Your administrator requires MFA on this account; it cannot be disabled here.')) : setShowDisable(true)}
            className="text-sm font-bold text-red-600 hover:text-red-700"
          >
            {t('settings.mfa_disable', 'Disable two-factor authentication')}
          </button>
        </div>
      )}

      {status?.totp_enabled && showDisable && (
        <form onSubmit={disable} className="space-y-3 border-t border-gray-100 dark:border-dark-border pt-4">
          <div>
            <label htmlFor="mfa-disable-password" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              {t('mfa.disable_password_label', 'Confirm your password to disable MFA')}
            </label>
            <input
              id="mfa-disable-password"
              type="password"
              required
              autoFocus
              value={disablePassword}
              onChange={(e) => { setDisablePassword(e.target.value); setError(null); }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              placeholder="••••••••"
            />
          </div>
          <div className="flex space-x-2">
            <button
              type="button"
              onClick={() => { setShowDisable(false); setDisablePassword(''); setError(null); }}
              className="px-4 py-2 border border-gray-200 dark:border-dark-border rounded-lg font-bold text-sm text-gray-600 dark:text-dark-muted"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={busy}
              className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 font-bold text-sm disabled:opacity-50"
            >
              {t('settings.mfa_disable', 'Disable two-factor authentication')}
            </button>
          </div>
        </form>
      )}
    </div>
  );

  const sessionsCard = (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-4">
      <div className="flex items-center space-x-3">
        <Monitor className="w-4 h-4 text-gray-400" />
        <div>
          <p className="text-sm font-medium text-gray-900 dark:text-dark-text">
            {t('settings.sessions_title', 'Active sessions')}
          </p>
          <p className="text-sm text-gray-500 dark:text-dark-muted">
            {t('settings.sessions_desc', 'Devices currently signed in to your account. Revoking one signs that device out.')}
          </p>
        </div>
      </div>

      {sessions === null ? (
        <div className="flex items-center justify-center py-4 text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
        </div>
      ) : (
        <AccountSessionList
          rows={sessionRows}
          onRevoke={revokeSessionById}
          busyId={revoking ?? undefined}
          labels={{
            current: t('settings.sessions_current', 'This device'),
            revoke: t('settings.sessions_revoke', 'Revoke'),
            revoking: t('settings.sessions_revoking', 'Revoking…'),
            empty: t('settings.sessions_empty', 'No other active sessions.'),
          }}
          headerAction={
            sessions.length > 1 ? (
              <button
                type="button"
                onClick={revokeOthers}
                disabled={revokingOthers}
                className="shrink-0 px-3 py-1.5 text-xs font-bold rounded-lg border border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-border disabled:opacity-50"
                data-testid="sessions-revoke-others"
              >
                {revokingOthers
                  ? t('settings.sessions_revoking', 'Revoking…')
                  : t('settings.sessions_revoke_others', 'Sign out others')}
              </button>
            ) : undefined
          }
        />
      )}
    </div>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('settings.nav_security', 'Security')}
        subtitle={t('settings.section_security', 'Security')}
        icon={<Shield className="w-8 h-8" />}
      />

      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <Lock className="w-4 h-4 text-gray-400" />
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-dark-text">{t('settings.change_password', 'Change Password')}</p>
              <p className="text-sm text-gray-500 dark:text-dark-muted">
                {t('settings.change_password_desc', 'Update your account password')}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              setPasswordError(null);
              setShowPasswordModal(true);
            }}
            className="px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg hover:bg-gray-50 dark:hover:bg-dark-border"
          >
            {t('common.edit', 'Change')}
          </button>
        </div>
      </div>

      <FormModal
        open={showPasswordModal}
        onOpenChange={(open) => {
          setShowPasswordModal(open);
          if (!open) setPasswordError(null);
        }}
        title={t('settings.change_password', 'Change Password')}
        icon={Lock}
        size="sm"
        onSubmit={changePassword}
        submitting={changingPassword}
        submitDisabled={!currentPassword || !newPassword || !confirmPassword || newPassword !== confirmPassword}
        submitLabel={t('settings.change_password_submit', 'Update password')}
        cancelLabel={t('common.cancel', 'Cancel')}
        bodyClassName="p-6 space-y-4"
      >
        <div>
          <label htmlFor="change-password-current" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
            {t('settings.change_password_current', 'Current password')}
          </label>
          <input
            id="change-password-current"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(e) => { setCurrentPassword(e.target.value); setPasswordError(null); }}
            className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
            placeholder="••••••••"
          />
        </div>
        <div>
          <label htmlFor="change-password-new" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
            {t('settings.change_password_new', 'New password')}
          </label>
          <input
            id="change-password-new"
            type="password"
            autoComplete="new-password"
            required
            value={newPassword}
            onChange={(e) => { setNewPassword(e.target.value); setPasswordError(null); }}
            className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
            placeholder="••••••••"
          />
        </div>
        <div>
          <label htmlFor="change-password-confirm" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
            {t('settings.change_password_confirm', 'Confirm new password')}
          </label>
          <input
            id="change-password-confirm"
            type="password"
            autoComplete="new-password"
            required
            value={confirmPassword}
            onChange={(e) => { setConfirmPassword(e.target.value); setPasswordError(null); }}
            className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
            placeholder="••••••••"
          />
        </div>
        {passwordMismatch && (
          <p className="text-sm text-red-600 dark:text-red-400">
            {t('settings.change_password_mismatch', 'Passwords do not match.')}
          </p>
        )}
        <ErrorBanner message={passwordError} />
      </FormModal>

      {loading ? (
        <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 flex items-center justify-center text-gray-400">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : (
        <>
          {mfaCard}
          <PasskeyCard onChanged={refresh} />
          {sessionsCard}
          <ApiTokensCard />
          <MyElevationsCard />
        </>
      )}
    </div>
  );
}

export default Security;
