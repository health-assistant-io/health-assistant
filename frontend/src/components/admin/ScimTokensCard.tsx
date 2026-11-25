import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { KeyRound, Loader2, Plus, Trash2 } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { SecretOnceReveal } from '../settings/SecretOnceReveal';
import { useUIStore } from '../../store/slices/uiSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import {
  createScimToken,
  listScimTokens,
  revokeScimToken,
  type ScimToken,
  type ScimTokenCreated,
} from '../../services/ssoService';

import { formatTimestamp } from '../../lib/dateFormats';
function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : formatTimestamp(date);
}

interface ScimTokensCardProps {
  tenantId: string;
  canWrite: boolean;
}

/**
 * Tenant SCIM credentials (program 12B): create with a **one-time**
 * plaintext, list hash-free projections, revoke. The plaintext exists only
 * in the creation modal — never in a list, a log or storage.
 */
export function ScimTokensCard({ tenantId, canWrite }: ScimTokensCardProps) {
  const { t } = useTranslation();
  const tRef = useRef(t);
  tRef.current = t;
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const [tokens, setTokens] = useState<ScimToken[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [created, setCreated] = useState<ScimTokenCreated | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setTokens(await listScimTokens(tenantId));
    } catch (err) {
      setLoadError(apiErrorDetail(err) || tRef.current('enterprise.scim.load_failed', 'Could not load SCIM tokens.'));
      setTokens([]);
    }
  }, [tenantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setName('');
    setExpiresAt('');
    setCreateError(null);
    setCreated(null);
    setShowCreate(true);
  };

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      const result = await createScimToken(tenantId, {
        name,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      });
      setCreated(result);
      setName('');
      setExpiresAt('');
      await load();
    } catch (err) {
      setCreateError(apiErrorDetail(err) || t('enterprise.scim.create_failed', 'Could not create the SCIM token.'));
    } finally {
      setCreating(false);
    }
  };

  const revoke = (token: ScimToken) => {
    showConfirmation({
      title: t('enterprise.scim.revoke_title', 'Revoke SCIM token'),
      message: t('enterprise.scim.revoke_message', {
        name: token.name,
        defaultValue: `Revoke ${token.name}? The directory connector stops working on its next request.`,
      }),
      confirmLabel: t('enterprise.scim.revoke', 'Revoke'),
      cancelLabel: t('common.cancel', 'Cancel'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await revokeScimToken(tenantId, token.id);
          toast.success(t('enterprise.scim.revoked_toast', 'SCIM token revoked.'));
          await load();
        } catch (err) {
          toast.error(apiErrorDetail(err) || t('enterprise.scim.revoke_failed', 'Could not revoke the SCIM token.'));
        }
      },
    });
  };

  return (
    <div
      className="bg-white dark:bg-dark-surface rounded-3xl shadow-xs border border-gray-100 dark:border-dark-border overflow-hidden"
      data-testid="scim-tokens-card"
    >
      <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between gap-4 bg-gray-50/30 dark:bg-dark-bg/20">
        <div>
          <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">
            {t('enterprise.scim.title', 'SCIM directory tokens')}
          </h3>
          <p className="text-sm text-gray-500 dark:text-dark-muted mt-1">
            {t('enterprise.scim.subtitle', 'Let a directory connector provision and deprovision users. The secret is shown once.')}
          </p>
        </div>
        {canWrite && (
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold shrink-0"
            data-testid="scim-add"
          >
            <Plus className="w-4 h-4" />
            {t('enterprise.scim.add', 'Create token')}
          </button>
        )}
      </div>

      <div className="p-8 space-y-4">
        {loadError && (
          <p className="text-sm text-red-600 dark:text-red-400" data-testid="scim-load-error">{loadError}</p>
        )}

        {tokens === null ? (
          <div className="flex items-center justify-center py-6 text-gray-400">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : tokens.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-dark-muted" data-testid="scim-empty">
            {t('enterprise.scim.empty', 'No SCIM tokens yet.')}
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-dark-border">
            {tokens.map((token) => (
              <li key={token.id} className="py-3 flex items-center justify-between gap-4" data-testid={`scim-token-${token.id}`}>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <KeyRound className="w-4 h-4 text-gray-400 shrink-0" />
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">{token.name}</p>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide ${
                        token.active
                          ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                          : 'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted'
                      }`}
                    >
                      {token.active
                        ? t('enterprise.scim.active', 'Active')
                        : t('enterprise.scim.inactive', 'Revoked')}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">
                    {t('enterprise.scim.last_used', 'Last used')}: {formatDate(token.last_used_at)} ·{' '}
                    {t('enterprise.scim.expires', 'Expires')}: {formatDate(token.expires_at)}
                  </p>
                </div>
                {canWrite && token.active && (
                  <button
                    type="button"
                    onClick={() => revoke(token)}
                    className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg border border-red-200 text-red-600 hover:bg-red-50 dark:border-red-900/40 dark:text-red-400 dark:hover:bg-red-900/20"
                    data-testid={`scim-revoke-${token.id}`}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    {t('enterprise.scim.revoke', 'Revoke')}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <Modal
        open={showCreate && !created}
        onOpenChange={(open) => {
          setShowCreate(open);
          if (!open) setCreated(null);
        }}
        title={t('enterprise.scim.add_title', 'Create SCIM token')}
        className="max-w-lg"
      >
        <form onSubmit={create} className="p-6 space-y-4">
          <div>
            <label htmlFor="scim-name" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('enterprise.scim.name', 'Token name')}
            </label>
            <input
              id="scim-name"
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              placeholder={t('enterprise.scim.name_placeholder', 'Okta production connector')}
            />
          </div>
          <div>
            <label htmlFor="scim-expires" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('enterprise.scim.expiry', 'Expires (optional)')}
            </label>
            <input
              id="scim-expires"
              type="datetime-local"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
            />
          </div>
          {createError && <p className="text-sm text-red-600 dark:text-red-400">{createError}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold text-gray-600 dark:text-dark-muted"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={creating || !name}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold disabled:opacity-50"
            >
              {creating && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('enterprise.scim.create', 'Create')}
            </button>
          </div>
        </form>
      </Modal>

      <SecretOnceReveal
        open={showCreate && created !== null}
        onClose={() => {
          setShowCreate(false);
          setCreated(null);
        }}
        title={t('enterprise.scim.created_title', 'SCIM token created')}
        warning={t('enterprise.scim.one_time', 'Copy this token now. It is shown once and cannot be recovered.')}
        secret={created?.token ?? ''}
        testId="scim-created"
        copyLabel={t('mfa.copy', 'Copy')}
        copiedLabel={t('common.copied', 'Copied')}
        className="max-w-lg"
        footer={
          <button
            type="button"
            onClick={() => {
              setShowCreate(false);
              setCreated(null);
            }}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold"
          >
            {t('common.done', 'Done')}
          </button>
        }
      />
    </div>
  );
}
