import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import {
  CheckCircle2,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import { Modal } from '../ui/Modal';
import { useUIStore } from '../../store/slices/uiSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import {
  createSsoConfig,
  deleteSsoConfig,
  listSsoConfigs,
  testSsoConnection,
  updateSsoConfig,
  type SsoConfig,
  type SsoConfigPayload,
  type SsoRole,
  type SsoTestConnectionResult,
} from '../../services/ssoService';

const ROLES: SsoRole[] = ['USER', 'MANAGER', 'ADMIN'];

interface Draft {
  issuer: string;
  client_id: string;
  client_secret_ref: string;
  allowed_domains: string;
  default_role: SsoRole;
  group_role_map: string;
  jit_enabled: boolean;
  enabled: boolean;
  scopes: string;
}

const EMPTY_DRAFT: Draft = {
  issuer: '',
  client_id: '',
  client_secret_ref: '',
  allowed_domains: '',
  default_role: 'USER',
  group_role_map: '',
  jit_enabled: true,
  enabled: false,
  scopes: '',
};

function toDraft(config: SsoConfig): Draft {
  return {
    issuer: config.issuer,
    client_id: config.client_id,
    client_secret_ref: config.client_secret_ref,
    allowed_domains: config.allowed_domains.join('\n'),
    default_role: config.default_role,
    group_role_map: Object.entries(config.group_role_map ?? {})
      .map(([group, role]) => `${group}=${role}`)
      .join('\n'),
    jit_enabled: config.jit_enabled,
    enabled: config.enabled,
    scopes: (config.scopes ?? []).join(' '),
  };
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

/** Parse `group=ROLE` lines (or a JSON object) into the map the API wants. */
export function parseGroupRoleMap(value: string): Record<string, string> {
  const trimmed = value.trim();
  if (!trimmed) return {};
  if (trimmed.startsWith('{')) {
    const parsed = JSON.parse(trimmed) as Record<string, unknown>;
    return Object.fromEntries(
      Object.entries(parsed).map(([group, role]) => [group, String(role)]),
    );
  }
  return Object.fromEntries(
    splitList(trimmed).map((line) => {
      const index = line.indexOf('=');
      if (index <= 0) throw new Error(`Invalid group mapping: ${line}`);
      return [line.slice(0, index).trim(), line.slice(index + 1).trim()];
    }),
  );
}

function buildPayload(draft: Draft): SsoConfigPayload {
  return {
    issuer: draft.issuer.trim(),
    client_id: draft.client_id.trim(),
    client_secret_ref: draft.client_secret_ref.trim(),
    allowed_domains: splitList(draft.allowed_domains),
    default_role: draft.default_role,
    group_role_map: parseGroupRoleMap(draft.group_role_map),
    jit_enabled: draft.jit_enabled,
    enabled: draft.enabled,
    scopes: draft.scopes.split(/\s+/).filter(Boolean),
  };
}

interface SsoConfigCardProps {
  tenantId: string;
  canWrite: boolean;
}

/**
 * Per-tenant OIDC bindings (program 12B): list, create, edit, enable /
 * disable, test connection and unbind. The client secret is never sent or
 * shown — the form carries a keyring slot name only.
 */
export function SsoConfigCard({ tenantId, canWrite }: SsoConfigCardProps) {
  const { t } = useTranslation();
  const tRef = useRef(t);
  tRef.current = t;
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const [configs, setConfigs] = useState<SsoConfig[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<SsoConfig | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, SsoTestConnectionResult>>({});

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setConfigs(await listSsoConfigs(tenantId));
    } catch (err) {
      setLoadError(apiErrorDetail(err) || tRef.current('enterprise.sso.load_failed', 'Could not load SSO providers.'));
      setConfigs([]);
    }
  }, [tenantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setFormError(null);
    setShowForm(true);
  };

  const openEdit = (config: SsoConfig) => {
    setEditing(config);
    setDraft(toDraft(config));
    setFormError(null);
    setShowForm(true);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setFormError(null);
    try {
      const payload = buildPayload(draft);
      if (editing) {
        await updateSsoConfig(tenantId, editing.id, payload);
        toast.success(t('enterprise.sso.updated_toast', 'SSO provider updated.'));
      } else {
        await createSsoConfig(tenantId, payload);
        toast.success(t('enterprise.sso.created_toast', 'SSO provider added (starts disabled).'));
      }
      setShowForm(false);
      await load();
    } catch (err) {
      setFormError(
        err instanceof SyntaxError
          ? t('enterprise.sso.group_map_invalid', 'Group mapping must be `group=ROLE` per line or a JSON object.')
          : apiErrorDetail(err) || t('enterprise.sso.save_failed', 'Could not save the SSO provider.'),
      );
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (config: SsoConfig) => {
    try {
      await updateSsoConfig(tenantId, config.id, { enabled: !config.enabled });
      toast.success(
        config.enabled
          ? t('enterprise.sso.disabled_toast', 'SSO disabled.')
          : t('enterprise.sso.enabled_toast', 'SSO enabled.'),
      );
      await load();
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('enterprise.sso.save_failed', 'Could not save the SSO provider.'));
    }
  };

  const remove = (config: SsoConfig) => {
    showConfirmation({
      title: t('enterprise.sso.delete_title', 'Remove SSO provider'),
      message: t('enterprise.sso.delete_message', {
        issuer: config.issuer,
        defaultValue: `Remove ${config.issuer}? Existing linked accounts keep their link, but the flow stops working.`,
      }),
      confirmLabel: t('common.delete', 'Delete'),
      cancelLabel: t('common.cancel', 'Cancel'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteSsoConfig(tenantId, config.id);
          toast.success(t('enterprise.sso.deleted_toast', 'SSO provider removed.'));
          await load();
        } catch (err) {
          toast.error(apiErrorDetail(err) || t('enterprise.sso.delete_failed', 'Could not remove the SSO provider.'));
        }
      },
    });
  };

  const test = async (config: SsoConfig) => {
    setTesting(config.id);
    try {
      const result = await testSsoConnection(tenantId, config.id);
      setTestResults((current) => ({ ...current, [config.id]: result }));
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('enterprise.sso.test_failed', 'Connection test failed.'));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div
      className="bg-white dark:bg-dark-surface rounded-3xl shadow-xs border border-gray-100 dark:border-dark-border overflow-hidden"
      data-testid="sso-config-card"
    >
      <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between gap-4 bg-gray-50/30 dark:bg-dark-bg/20">
        <div>
          <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">
            {t('enterprise.sso.title', 'Single sign-on (OIDC)')}
          </h3>
          <p className="text-sm text-gray-500 dark:text-dark-muted mt-1">
            {t('enterprise.sso.subtitle', 'Link this tenant to an identity provider. Accounts are linked by verified email and never merged.')}
          </p>
        </div>
        {canWrite && (
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold shrink-0"
            data-testid="sso-add"
          >
            <Plus className="w-4 h-4" />
            {t('enterprise.sso.add', 'Add provider')}
          </button>
        )}
      </div>

      <div className="p-8 space-y-4">
        {loadError && (
          <p className="text-sm text-red-600 dark:text-red-400" data-testid="sso-load-error">{loadError}</p>
        )}

        {configs === null ? (
          <div className="flex items-center justify-center py-6 text-gray-400">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : configs.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-dark-muted" data-testid="sso-empty">
            {t('enterprise.sso.empty', 'No identity provider is bound to this tenant.')}
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-dark-border">
            {configs.map((config) => {
              const result = testResults[config.id];
              return (
                <li key={config.id} className="py-4 space-y-3" data-testid={`sso-config-${config.id}`}>
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <KeyRound className="w-4 h-4 text-gray-400 shrink-0" />
                        <p className="text-sm font-bold text-gray-900 dark:text-dark-text break-all">
                          {config.issuer}
                        </p>
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide ${
                            config.enabled
                              ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                              : 'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted'
                          }`}
                        >
                          {config.enabled
                            ? t('enterprise.sso.enabled', 'Enabled')
                            : t('enterprise.sso.disabled', 'Disabled')}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">
                        {t('enterprise.sso.client', 'Client')}: {config.client_id} ·{' '}
                        {t('enterprise.sso.secret_slot', 'Secret slot')}: {config.client_secret_ref}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-dark-muted">
                        {t('enterprise.sso.domains', 'Domains')}: {config.allowed_domains.join(', ') || '—'} ·{' '}
                        {t('enterprise.sso.default_role', 'Default role')}: {config.default_role}
                      </p>
                    </div>
                    {canWrite && (
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          type="button"
                          onClick={() => toggleEnabled(config)}
                          className="px-2.5 py-1.5 text-xs font-bold rounded-lg border border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-border"
                          data-testid={`sso-toggle-${config.id}`}
                        >
                          {config.enabled
                            ? t('enterprise.sso.disable', 'Disable')
                            : t('enterprise.sso.enable', 'Enable')}
                        </button>
                        <button
                          type="button"
                          onClick={() => openEdit(config)}
                          className="p-1.5 text-gray-400 hover:text-blue-600 rounded-lg"
                          aria-label={t('common.edit', 'Edit')}
                          data-testid={`sso-edit-${config.id}`}
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => remove(config)}
                          className="p-1.5 text-gray-400 hover:text-red-600 rounded-lg"
                          aria-label={t('common.delete', 'Delete')}
                          data-testid={`sso-delete-${config.id}`}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      onClick={() => test(config)}
                      disabled={testing === config.id}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg border border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-border disabled:opacity-50"
                      data-testid={`sso-test-${config.id}`}
                    >
                      {testing === config.id ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="w-3.5 h-3.5" />
                      )}
                      {t('enterprise.sso.test', 'Test connection')}
                    </button>
                    {result && (
                      <div className="flex flex-wrap items-center gap-3 text-xs" data-testid={`sso-test-result-${config.id}`}>
                        <span className={result.discovery_ok ? 'text-green-700 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                          {result.discovery_ok ? <CheckCircle2 className="w-3.5 h-3.5 inline mr-1" /> : <XCircle className="w-3.5 h-3.5 inline mr-1" />}
                          {t('enterprise.sso.discovery', 'Discovery')}
                        </span>
                        <span className={result.secret_resolved ? 'text-green-700 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                          {result.secret_resolved ? <CheckCircle2 className="w-3.5 h-3.5 inline mr-1" /> : <XCircle className="w-3.5 h-3.5 inline mr-1" />}
                          {t('enterprise.sso.secret', 'Secret slot')}
                        </span>
                        {result.error && (
                          <span className="text-red-600 dark:text-red-400">{result.error}</span>
                        )}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <Modal
        open={showForm}
        onOpenChange={(open) => setShowForm(open)}
        title={
          editing
            ? t('enterprise.sso.edit_title', 'Edit SSO provider')
            : t('enterprise.sso.add_title', 'Add SSO provider')
        }
        className="max-w-2xl"
      >
        <form onSubmit={save} className="p-6 space-y-4" data-testid="sso-form">
          <div>
            <label htmlFor="sso-issuer" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('enterprise.sso.issuer', 'Issuer URL')}
            </label>
            <input
              id="sso-issuer"
              type="url"
              required
              value={draft.issuer}
              onChange={(e) => setDraft({ ...draft, issuer: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              placeholder="https://login.example.com"
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="sso-client-id" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                {t('enterprise.sso.client_id', 'Client ID')}
              </label>
              <input
                id="sso-client-id"
                type="text"
                required
                value={draft.client_id}
                onChange={(e) => setDraft({ ...draft, client_id: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              />
            </div>
            <div>
              <label htmlFor="sso-secret-ref" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                {t('enterprise.sso.secret_ref', 'Secret slot name')}
              </label>
              <input
                id="sso-secret-ref"
                type="text"
                required
                value={draft.client_secret_ref}
                onChange={(e) => setDraft({ ...draft, client_secret_ref: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text font-mono"
                placeholder="tenant-sso/clinic"
              />
              <p className="text-xs text-gray-400 mt-1">
                {t('enterprise.sso.secret_ref_hint', 'A keyring slot name — never the secret value itself.')}
              </p>
            </div>
          </div>
          <div>
            <label htmlFor="sso-domains" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('enterprise.sso.allowed_domains', 'Allowed email domains')}
            </label>
            <textarea
              id="sso-domains"
              required
              rows={2}
              value={draft.allowed_domains}
              onChange={(e) => setDraft({ ...draft, allowed_domains: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text font-mono text-sm"
              placeholder={'clinic.example.com\nhospital.example.com'}
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="sso-default-role" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                {t('enterprise.sso.default_role_label', 'Default role')}
              </label>
              <select
                id="sso-default-role"
                value={draft.default_role}
                onChange={(e) => setDraft({ ...draft, default_role: e.target.value as SsoRole })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              >
                {ROLES.map((role) => (
                  <option key={role} value={role}>{role}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="sso-scopes" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
                {t('enterprise.sso.scopes', 'Scopes')}
              </label>
              <input
                id="sso-scopes"
                type="text"
                value={draft.scopes}
                onChange={(e) => setDraft({ ...draft, scopes: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text font-mono text-sm"
                placeholder="openid email profile"
              />
            </div>
          </div>
          <div>
            <label htmlFor="sso-group-map" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('enterprise.sso.group_map', 'Group → role mapping')}
            </label>
            <textarea
              id="sso-group-map"
              rows={3}
              value={draft.group_role_map}
              onChange={(e) => setDraft({ ...draft, group_role_map: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text font-mono text-sm"
              placeholder={'clinic-admins=ADMIN\nclinic-managers=MANAGER'}
            />
          </div>
          <div className="flex flex-wrap gap-6">
            <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-dark-muted">
              <input
                type="checkbox"
                checked={draft.jit_enabled}
                onChange={(e) => setDraft({ ...draft, jit_enabled: e.target.checked })}
              />
              {t('enterprise.sso.jit', 'Provision new accounts on first sign-in')}
            </label>
            <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-dark-muted">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
              />
              {t('enterprise.sso.enabled_label', 'Enable this provider')}
            </label>
          </div>
          {formError && <p className="text-sm text-red-600 dark:text-red-400">{formError}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="px-4 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold text-gray-600 dark:text-dark-muted"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold disabled:opacity-50"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('common.save', 'Save')}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
