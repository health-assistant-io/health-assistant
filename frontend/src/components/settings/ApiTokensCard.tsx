import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { KeySquare, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { SecretOnceReveal } from '../settings/SecretOnceReveal';
import { useUIStore } from '../../store/slices/uiSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import {
  createApiToken,
  getApiTokenPermissions,
  listApiTokens,
  revokeApiToken,
  rotateApiToken,
  type ApiToken,
  type ApiTokenCreated,
  type ApiTokenPatientScope,
  type ApiTokenPermissionCatalog,
} from '../../services/apiTokenService';

import { formatTimestamp } from '../../lib/dateFormats';
const PATIENT_SCOPES: ApiTokenPatientScope[] = ['all', 'granted', 'none'];

function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : formatTimestamp(date);
}

function tokenState(token: ApiToken): 'active' | 'revoked' | 'expired' {
  if (token.revoked_at) return 'revoked';
  if (token.expires_at && new Date(token.expires_at).getTime() <= Date.now()) return 'expired';
  return 'active';
}

function parsePatientIds(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

/**
 * Scoped personal API tokens (program 12C2B): create with a permission
 * subset and optional patient scope, copy the plaintext once, rotate and
 * revoke. The server re-validates every write; the plaintext never touches
 * storage or logs.
 */
export function ApiTokensCard() {
  const { t } = useTranslation();
  const tRef = useRef(t);
  tRef.current = t;
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [catalog, setCatalog] = useState<ApiTokenPermissionCatalog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [patientScope, setPatientScope] = useState<ApiTokenPatientScope>('all');
  const [patientIds, setPatientIds] = useState('');
  const [selected, setSelected] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [tokenRows, catalogData] = await Promise.all([
        listApiTokens(),
        getApiTokenPermissions(),
      ]);
      setTokens(tokenRows);
      setCatalog(catalogData);
    } catch (err) {
      setLoadError(apiErrorDetail(err) || tRef.current('settings.api_tokens_load_failed', 'Could not load API tokens.'));
      setTokens([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = useMemo(() => {
    const groups = new Map<string, string[]>();
    (catalog?.permissions ?? []).forEach((option) => {
      const bucket = groups.get(option.scope) ?? [];
      bucket.push(option.permission);
      groups.set(option.scope, bucket);
    });
    return Array.from(groups.entries());
  }, [catalog]);

  const openCreate = () => {
    setName('');
    setExpiresAt('');
    setPatientScope('all');
    setPatientIds('');
    setSelected([]);
    setFormError(null);
    setCreated(null);
    setShowCreate(true);
  };

  const submitCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || selected.length === 0) return;
    setBusy(true);
    setFormError(null);
    try {
      const result = await createApiToken({
        name: name.trim(),
        permissions: selected,
        patient_scope: patientScope,
        patient_ids: patientScope === 'granted' ? parsePatientIds(patientIds) : [],
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      });
      setCreated(result);
      toast.success(t('settings.api_tokens_created', 'API token created.'));
      await load();
    } catch (err) {
      setFormError(apiErrorDetail(err) || t('settings.api_tokens_create_failed', 'Could not create the token.'));
    } finally {
      setBusy(false);
    }
  };

  const rotate = (token: ApiToken) => {
    showConfirmation({
      title: t('settings.api_tokens_rotate_title', 'Rotate API token'),
      message: t('settings.api_tokens_rotate_message', {
        name: token.name,
        defaultValue: `Rotate ${token.name}? The current secret stops working immediately; the replacement is shown once.`,
      }),
      confirmLabel: t('settings.api_tokens_rotate', 'Rotate'),
      cancelLabel: t('common.cancel', 'Cancel'),
      onConfirm: async () => {
        try {
          const result = await rotateApiToken(token.id);
          setCreated(result);
          setShowCreate(true);
          toast.success(t('settings.api_tokens_rotated', 'API token rotated.'));
          await load();
        } catch (err) {
          toast.error(apiErrorDetail(err) || t('settings.api_tokens_rotate_failed', 'Could not rotate the token.'));
        }
      },
    });
  };

  const revoke = (token: ApiToken) => {
    showConfirmation({
      title: t('settings.api_tokens_revoke_title', 'Revoke API token'),
      message: t('settings.api_tokens_revoke_message', {
        name: token.name,
        defaultValue: `Revoke ${token.name}? The bearer stops working immediately.`,
      }),
      confirmLabel: t('settings.api_tokens_revoke', 'Revoke'),
      cancelLabel: t('common.cancel', 'Cancel'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await revokeApiToken(token.id);
          toast.success(t('settings.api_tokens_revoked', 'API token revoked.'));
          await load();
        } catch (err) {
          toast.error(apiErrorDetail(err) || t('settings.api_tokens_revoke_failed', 'Could not revoke the token.'));
        }
      },
    });
  };

  return (
    <div
      className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-4"
      data-testid="api-tokens-card"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <KeySquare className="w-4 h-4 text-gray-400" />
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-dark-text">
              {t('settings.api_tokens_title', 'API tokens')}
            </p>
            <p className="text-sm text-gray-500 dark:text-dark-muted">
              {t('settings.api_tokens_desc', 'Scoped credentials for scripts and integrations. The secret is shown once.')}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={openCreate}
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-bold"
          data-testid="api-token-add"
        >
          <Plus className="w-4 h-4" />
          {t('settings.api_tokens_add', 'Create token')}
        </button>
      </div>

      {loadError && <p className="text-sm text-red-600 dark:text-red-400">{loadError}</p>}

      {tokens === null ? (
        <div className="flex items-center justify-center py-4 text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
        </div>
      ) : tokens.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-dark-muted" data-testid="api-tokens-empty">
          {t('settings.api_tokens_empty', 'No API tokens yet.')}
        </p>
      ) : (
        <ul className="divide-y divide-gray-100 dark:divide-dark-border" data-testid="api-tokens-list">
          {tokens.map((token) => {
            const state = tokenState(token);
            return (
              <li key={token.id} className="py-3 flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-medium text-gray-900 dark:text-dark-text truncate">
                      {token.name}
                    </p>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide ${
                        state === 'active'
                          ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                          : 'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted'
                      }`}
                    >
                      {t(`settings.api_tokens_state_${state}`, state)}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 dark:text-dark-muted">
                    <code className="font-mono">{token.token_prefix}…</code> ·{' '}
                    {t('settings.api_tokens_permissions_count', {
                      count: token.permissions.length,
                      defaultValue: `${token.permissions.length} permissions`,
                    })} ·{' '}
                    {t('settings.api_tokens_patient_scope', 'Patients')}:{' '}
                    {t(`settings.api_tokens_scope_${token.patient_scope}`, token.patient_scope)}
                  </p>
                  <p className="text-xs text-gray-500 dark:text-dark-muted">
                    {t('settings.api_tokens_last_used', 'Last used')}: {formatDate(token.last_used_at)} ·{' '}
                    {t('settings.api_tokens_expires', 'Expires')}: {formatDate(token.expires_at)}
                  </p>
                </div>
                {state === 'active' && (
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={() => rotate(token)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg border border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-border"
                      data-testid={`api-token-rotate-${token.id}`}
                    >
                      <RefreshCw className="w-3.5 h-3.5" />
                      {t('settings.api_tokens_rotate', 'Rotate')}
                    </button>
                    <button
                      type="button"
                      onClick={() => revoke(token)}
                      className="p-1.5 text-gray-400 hover:text-red-600 rounded-lg"
                      aria-label={t('settings.api_tokens_revoke', 'Revoke')}
                      data-testid={`api-token-revoke-${token.id}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Modal
        open={showCreate && !created}
        onOpenChange={(open) => {
          setShowCreate(open);
          if (!open) setCreated(null);
        }}
        title={t('settings.api_tokens_create_title', 'Create API token')}
        className="max-w-2xl"
      >
        <form onSubmit={submitCreate} className="p-6 space-y-4" data-testid="api-token-form">
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="api-token-name" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                {t('settings.api_tokens_name', 'Name')}
              </label>
              <input
                id="api-token-name"
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
                placeholder={t('settings.api_tokens_name_placeholder', 'Backup script')}
              />
            </div>
            <div>
              <label htmlFor="api-token-expires" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                {t('settings.api_tokens_expiry', 'Expires (optional)')}
              </label>
              <input
                id="api-token-expires"
                type="datetime-local"
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              />
            </div>
          </div>

          <div>
            <label htmlFor="api-token-patient-scope" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('settings.api_tokens_patient_scope_label', 'Patient scope')}
            </label>
            <select
              id="api-token-patient-scope"
              value={patientScope}
              onChange={(e) => setPatientScope(e.target.value as ApiTokenPatientScope)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
            >
              {PATIENT_SCOPES.map((scope) => (
                <option key={scope} value={scope}>
                  {t(`settings.api_tokens_scope_${scope}`, scope)}
                </option>
              ))}
            </select>
            {patientScope === 'granted' && (
              <div className="mt-2">
                <label htmlFor="api-token-patient-ids" className="block text-xs text-gray-500 dark:text-dark-muted mb-1">
                  {t('settings.api_tokens_patient_ids', 'Patient IDs (comma-separated)')}
                </label>
                <input
                  id="api-token-patient-ids"
                  type="text"
                  required
                  value={patientIds}
                  onChange={(e) => setPatientIds(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text font-mono text-xs"
                  placeholder="00000000-0000-0000-0000-000000000000"
                />
              </div>
            )}
          </div>

          <div>
            <p className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              {t('settings.api_tokens_permissions', 'Permissions')}
            </p>
            <div className="max-h-56 overflow-y-auto rounded-xl border border-gray-200 dark:border-dark-border p-3 space-y-3">
              {grouped.length === 0 && (
                <p className="text-xs text-gray-400">{t('settings.api_tokens_no_permissions', 'No permissions are assignable.')}</p>
              )}
              {grouped.map(([scope, permissions]) => (
                <div key={scope}>
                  <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">
                    {t(`settings.api_tokens_perm_scope_${scope}`, scope)}
                  </p>
                  <div className="grid gap-1 sm:grid-cols-2">
                    {permissions.map((permission) => (
                      <label key={permission} className="flex items-center gap-2 text-xs text-gray-700 dark:text-dark-muted">
                        <input
                          type="checkbox"
                          checked={selected.includes(permission)}
                          onChange={(e) =>
                            setSelected((current) =>
                              e.target.checked
                                ? [...current, permission]
                                : current.filter((item) => item !== permission),
                            )
                          }
                          data-testid={`api-token-permission-${permission}`}
                        />
                        <code className="font-mono">{permission}</code>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {formError && <p className="text-sm text-red-600 dark:text-red-400">{formError}</p>}
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
              disabled={busy || !name.trim() || selected.length === 0}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold disabled:opacity-50"
            >
              {busy && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('settings.api_tokens_create', 'Create')}
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
        title={t('settings.api_tokens_secret_title', 'Copy your API token')}
        warning={t('settings.api_tokens_secret_warning', 'This secret is shown once. Store it securely — it cannot be recovered.')}
        secret={created?.token ?? ''}
        testId="api-token-secret"
        copyLabel={t('mfa.copy', 'Copy')}
        copiedLabel={t('common.copied', 'Copied')}
        className="max-w-2xl"
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
