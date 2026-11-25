import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Fingerprint, KeyRound, Loader2, Plus, Trash2 } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { useUIStore } from '../../store/slices/uiSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import {
  deleteMyPasskey,
  listMyPasskeys,
  passkeysSupported,
  registerPasskey,
  type Passkey,
} from '../../services/passkeyService';

import { formatTimestamp } from '../../lib/dateFormats';
function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : formatTimestamp(date);
}

interface PasskeyCardProps {
  /** Refresh the factor-aware MFA status after a passkey changes. */
  onChanged?: () => void;
}

/**
 * Passkeys / WebAuthn self-service (program 12C2): list the registered
 * credentials, register one with a user-chosen name, delete one. Every
 * ceremony requires user verification. Registration and deletion are
 * step-up gated (`mfa_change`) — the global step-up prompt handles that
 * transparently. Deleting the last factor while MFA is required is refused
 * by the server.
 */
export function PasskeyCard({ onChanged }: PasskeyCardProps) {
  const { t } = useTranslation();
  const tRef = useRef(t);
  tRef.current = t;
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const supported = passkeysSupported();
  const [passkeys, setPasskeys] = useState<Passkey[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showRegister, setShowRegister] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!supported) {
      setPasskeys([]);
      return;
    }
    setLoadError(null);
    try {
      setPasskeys(await listMyPasskeys());
    } catch (err) {
      setLoadError(apiErrorDetail(err) || tRef.current('settings.passkeys_load_failed', 'Could not load passkeys.'));
      setPasskeys([]);
    }
  }, [supported]);

  useEffect(() => {
    void load();
  }, [load]);

  const openRegister = () => {
    setName(t('settings.passkeys_default_name', 'Passkey'));
    setError(null);
    setShowRegister(true);
  };

  const register = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await registerPasskey(name.trim());
      setShowRegister(false);
      toast.success(t('settings.passkeys_registered', 'Passkey registered.'));
      await load();
      onChanged?.();
    } catch (err) {
      setError(apiErrorDetail(err) || t('settings.passkeys_register_failed', 'Could not register the passkey.'));
    } finally {
      setBusy(false);
    }
  };

  const remove = (passkey: Passkey) => {
    showConfirmation({
      title: t('settings.passkeys_delete_title', 'Delete passkey'),
      message: t('settings.passkeys_delete_message', {
        name: passkey.name,
        defaultValue: `Delete ${passkey.name}? You will need another factor to sign in.`,
      }),
      confirmLabel: t('common.delete', 'Delete'),
      cancelLabel: t('common.cancel', 'Cancel'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteMyPasskey(passkey.id);
          toast.success(t('settings.passkeys_deleted', 'Passkey deleted.'));
          await load();
          onChanged?.();
        } catch (err) {
          toast.error(apiErrorDetail(err) || t('settings.passkeys_delete_failed', 'Could not delete the passkey.'));
        }
      },
    });
  };

  return (
    <div
      className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-4"
      data-testid="passkeys-card"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <Fingerprint className="w-4 h-4 text-gray-400" />
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-dark-text">
              {t('settings.passkeys_title', 'Passkeys')}
            </p>
            <p className="text-sm text-gray-500 dark:text-dark-muted">
              {t('settings.passkeys_desc', 'Sign in and prove sensitive actions with Touch ID, Windows Hello or a security key.')}
            </p>
          </div>
        </div>
        {supported && (
          <button
            type="button"
            onClick={openRegister}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-bold"
            data-testid="passkey-add"
          >
            <Plus className="w-4 h-4" />
            {t('settings.passkeys_add', 'Add passkey')}
          </button>
        )}
      </div>

      {!supported && (
        <p className="text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/40 rounded-xl px-3 py-2">
          {t('settings.passkeys_unsupported', 'This browser does not support passkeys. Use TOTP instead.')}
        </p>
      )}

      {loadError && <p className="text-sm text-red-600 dark:text-red-400">{loadError}</p>}

      {supported && passkeys !== null && passkeys.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-dark-muted" data-testid="passkeys-empty">
          {t('settings.passkeys_empty', 'No passkeys registered yet.')}
        </p>
      )}

      {supported && passkeys !== null && passkeys.length > 0 && (
        <ul className="divide-y divide-gray-100 dark:divide-dark-border" data-testid="passkeys-list">
          {passkeys.map((passkey) => (
            <li key={passkey.id} className="py-3 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-dark-text truncate">
                  {passkey.name}
                  {passkey.disabled && (
                    <span className="ml-2 text-[10px] font-black uppercase tracking-wide text-red-600 dark:text-red-400">
                      {t('settings.passkeys_disabled', 'Disabled')}
                    </span>
                  )}
                </p>
                <p className="text-xs text-gray-500 dark:text-dark-muted">
                  {t('settings.passkeys_created', 'Added')}: {formatDate(passkey.created_at)} ·{' '}
                  {t('settings.passkeys_last_used', 'Last used')}: {formatDate(passkey.last_used_at)}
                </p>
                <p className="text-[11px] text-gray-400">
                  {passkey.backup_eligible
                    ? t('settings.passkeys_synced', ' Synced / backup-eligible credential')
                    : t('settings.passkeys_device_bound', ' Device-bound credential')}
                </p>
              </div>
              <button
                type="button"
                onClick={() => remove(passkey)}
                className="shrink-0 p-1.5 text-gray-400 hover:text-red-600 rounded-lg"
                aria-label={t('common.delete', 'Delete')}
                data-testid={`passkey-delete-${passkey.id}`}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="text-[11px] text-gray-400 flex items-start gap-1.5">
        <KeyRound className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        {t('settings.passkeys_uv_note', 'User verification (biometric or PIN) is required for every passkey ceremony.')}
      </p>

      <Modal
        open={showRegister}
        onOpenChange={(open) => setShowRegister(open)}
        title={t('settings.passkeys_add_title', 'Register a passkey')}
        className="max-w-md"
      >
        <form onSubmit={register} className="p-6 space-y-4" data-testid="passkey-register-form">
          <p className="text-sm text-gray-500 dark:text-dark-muted">
            {t('settings.passkeys_add_hint', 'Your browser will ask for biometric or PIN verification. Name the credential so you can recognise it later.')}
          </p>
          <div>
            <label htmlFor="passkey-name" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('settings.passkeys_name', 'Passkey name')}
            </label>
            <input
              id="passkey-name"
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              placeholder={t('settings.passkeys_name_placeholder', 'MacBook Touch ID')}
            />
          </div>
          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowRegister(false)}
              className="px-4 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold text-gray-600 dark:text-dark-muted"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={busy || !name.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold disabled:opacity-50"
            >
              {busy && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('settings.passkeys_register', 'Register')}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
