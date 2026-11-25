import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { toast } from 'react-toastify';
import { KeyRound, Plus, Save, ScrollText, X } from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { useAccessStore } from '../../store/slices/accessSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import {
  POLICY_MFA_ROLES,
  getSecurityPolicy,
  updateSecurityPolicy,
  type TenantSecurityPolicy,
  type TenantSecurityPolicyPatchPayload,
} from '../../services/securityPolicyService';

import { formatTimestamp } from '../../lib/dateFormats';
/** A 422 `detail` entry of the policy patch endpoint. */
interface FieldError {
  field: string;
  msg: string;
}

/** Parse the policy endpoint's 422 validation array into per-field
 * errors (the `loc` path is `['body', '<field>', …]`). */
export function policyFieldErrors(err: unknown): FieldError[] {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (!Array.isArray(detail)) return [];
  return detail.flatMap((entry) => {
    const loc = (entry as { loc?: unknown[] })?.loc;
    const msg = (entry as { msg?: unknown })?.msg;
    if (!Array.isArray(loc) || typeof msg !== 'string') return [];
    const field = loc.filter((part) => typeof part === 'string' && part !== 'body').join('.');
    if (!field) return [];
    return [{ field, msg }];
  });
}

/** Lightweight CIDR sanity check (the backend canonicalizes + is the
 * authority; this only keeps obvious typos out of the list). */
export function isValidCidr(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed || /\s/.test(trimmed)) return false;
  const [addr, prefixRaw, ...rest] = trimmed.split('/');
  if (rest.length > 0) return false;
  if (prefixRaw !== undefined && !/^\d{1,3}$/.test(prefixRaw)) return false;
  const prefix = prefixRaw === undefined ? null : Number(prefixRaw);
  if (addr.includes(':')) {
    try {
      // eslint-disable-next-line no-new
      new URL(`http://[${addr}]/`);
    } catch {
      return false;
    }
    return prefix === null || prefix <= 128;
  }
  const octets = addr.split('.');
  if (octets.length !== 4) return false;
  if (!octets.every((o) => /^\d{1,3}$/.test(o) && Number(o) <= 255)) return false;
  return prefix === null || prefix <= 32;
}

interface Draft {
  mfa_required_roles: string[];
  session_idle_minutes: number | null;
  session_max_hours: number | null;
  concurrent_session_limit: number | null;
  ip_allowlist: string[];
  patient_sharing: boolean;
  manager_scope: 'tenant' | 'assigned';
}

function toDraft(policy: TenantSecurityPolicy): Draft {
  return {
    mfa_required_roles: [...policy.mfa_required_roles],
    session_idle_minutes: policy.session_idle_minutes,
    session_max_hours: policy.session_max_hours,
    concurrent_session_limit: policy.concurrent_session_limit,
    ip_allowlist: [...policy.ip_allowlist],
    patient_sharing: policy.patient_sharing,
    manager_scope: policy.manager_scope === 'assigned' ? 'assigned' : 'tenant',
  };
}

function buildPatch(policy: TenantSecurityPolicy, draft: Draft): TenantSecurityPolicyPatchPayload {
  const patch: TenantSecurityPolicyPatchPayload = {};
  const sameRoles =
    [...draft.mfa_required_roles].sort().join(',') ===
    [...policy.mfa_required_roles].sort().join(',');
  if (!sameRoles) patch.mfa_required_roles = draft.mfa_required_roles;
  if (draft.session_idle_minutes !== policy.session_idle_minutes) {
    patch.session_idle_minutes = draft.session_idle_minutes;
  }
  if (draft.session_max_hours !== policy.session_max_hours) {
    patch.session_max_hours = draft.session_max_hours;
  }
  if (draft.concurrent_session_limit !== policy.concurrent_session_limit) {
    patch.concurrent_session_limit = draft.concurrent_session_limit;
  }
  if (draft.ip_allowlist.join('\n') !== policy.ip_allowlist.join('\n')) {
    patch.ip_allowlist = draft.ip_allowlist;
  }
  if (draft.patient_sharing !== policy.patient_sharing) {
    patch.patient_sharing = draft.patient_sharing;
  }
  if (draft.manager_scope !== policy.manager_scope) {
    patch.manager_scope = draft.manager_scope;
  }
  return patch;
}

interface CardProps {
  tenantId: string;
  canWrite: boolean;
}

/**
 * The tenant security-policy editor (ADR 0054 Phase 3 UI). Saves a partial
 * patch of exactly the changed keys; an emptied nullable knob sends an
 * explicit `null` (clear = feature off). Read-only without
 * `settings:write` (MANAGER — the server would answer 403 anyway).
 */
export function TenantSecurityPolicyCard({ tenantId, canWrite }: CardProps) {
  const { t } = useTranslation();
  const showConfirmation = useUIStore((s) => s.showConfirmation);

  const [policy, setPolicy] = useState<TenantSecurityPolicy | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldError[]>([]);
  const [cidrInput, setCidrInput] = useState('');
  const [cidrError, setCidrError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const loaded = await getSecurityPolicy(tenantId);
        if (cancelled) return;
        setPolicy(loaded);
        setDraft(toDraft(loaded));
      } catch (err) {
        if (!cancelled) {
          setLoadError(apiErrorDetail(err) || t('admin.policy.load_failed'));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tenantId, t]);

  const dirty = useMemo(
    () => (policy && draft ? Object.keys(buildPatch(policy, draft)).length > 0 : false),
    [policy, draft],
  );

  const save = async () => {
    if (!policy || !draft) return;
    const patch = buildPatch(policy, draft);
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setFieldErrors([]);
    try {
      const updated = await updateSecurityPolicy(tenantId, patch);
      setPolicy(updated);
      setDraft(toDraft(updated));
      toast.success(t('admin.policy.saved_toast'));
    } catch (err) {
      const errors = policyFieldErrors(err);
      if (errors.length > 0) {
        setFieldErrors(errors);
        toast.error(t('admin.policy.validation_toast'));
      } else {
        toast.error(apiErrorDetail(err) || t('admin.policy.save_failed'));
      }
    } finally {
      setSaving(false);
    }
  };

  const addCidr = () => {
    if (!draft) return;
    const value = cidrInput.trim();
    if (!value) return;
    if (!isValidCidr(value)) {
      setCidrError(t('admin.policy.cidr_invalid'));
      return;
    }
    if (draft.ip_allowlist.includes(value)) {
      setCidrError(t('admin.policy.cidr_duplicate'));
      return;
    }
    setCidrError(null);
    setCidrInput('');
    setDraft({ ...draft, ip_allowlist: [...draft.ip_allowlist, value] });
  };

  const removeCidr = (entry: string) => {
    if (!draft) return;
    setDraft({ ...draft, ip_allowlist: draft.ip_allowlist.filter((item) => item !== entry) });
  };

  const requestRemoveCidr = (entry: string) => {
    showConfirmation({
      title: t('admin.policy.cidr_remove_title'),
      message: t('admin.policy.cidr_remove_message', { cidr: entry }),
      confirmLabel: t('admin.policy.cidr_remove_confirm'),
      cancelLabel: t('common.cancel'),
      confirmVariant: 'danger',
      onConfirm: () => removeCidr(entry),
    });
  };

  const fieldError = (field: string): string | null =>
    fieldErrors.find((error) => error.field === field)?.msg ?? null;

  const errorBanner = fieldErrors.length > 0 && (
    <div
      className="mb-6 rounded-xl border border-red-200 dark:border-red-900/40 bg-red-50 dark:bg-red-900/10 px-4 py-3 text-sm text-red-700 dark:text-red-400 space-y-1"
      data-testid="policy-error-banner"
    >
      {fieldErrors.map((error) => (
        <p key={`${error.field}:${error.msg}`}>{`${error.field}: ${error.msg}`}</p>
      ))}
    </div>
  );

  if (loading) return <LoadingState variant="inline" showText={false} message="" />;

  if (loadError || !policy || !draft) {
    return (
      <div
        className="rounded-xl border border-red-200 dark:border-red-900/40 bg-red-50 dark:bg-red-900/10 px-4 py-3 text-sm text-red-700 dark:text-red-400"
        data-testid="policy-load-error"
      >
        {loadError}
      </div>
    );
  }

  const numberField = (
    field: 'session_idle_minutes' | 'session_max_hours' | 'concurrent_session_limit',
    label: string,
    help: string,
    min: number,
    max: number,
  ) => {
    const value = draft[field];
    const error = fieldError(field);
    return (
      <div data-testid={`policy-field-${field}`}>
        <label htmlFor={`policy-${field}`} className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
          {label}
        </label>
        <p className="text-xs text-gray-500 dark:text-dark-muted mb-2">{help}</p>
        <input
          id={`policy-${field}`}
          type="number"
          min={min}
          max={max}
          disabled={!canWrite}
          value={value === null ? '' : value}
          onChange={(e) => {
            const raw = e.target.value;
            setDraft({
              ...draft,
              [field]: raw === '' ? null : Number(raw),
            });
          }}
          className="w-40 px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text disabled:opacity-60"
        />
        {error && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{error}</p>}
      </div>
    );
  };

  return (
    <div
      className="bg-white dark:bg-dark-surface rounded-3xl shadow-xs border border-gray-100 dark:border-dark-border overflow-hidden"
      data-testid="security-policy-card"
    >
      <div className="px-8 py-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between bg-gray-50/30 dark:bg-dark-bg/20">
        <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text tracking-tight">
          {t('admin.policy.title')}
        </h3>
        <div className="flex items-center gap-3">
          {canWrite ? (
            <button
              type="button"
              onClick={save}
              disabled={!dirty || saving}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="policy-save"
            >
              <Save className="w-4 h-4" />
              {saving ? t('admin.policy.saving') : t('common.save')}
            </button>
          ) : (
            <span className="px-2.5 py-1 rounded-full bg-gray-100 dark:bg-dark-bg text-[10px] font-black uppercase tracking-widest text-gray-400">
              {t('admin.policy.read_only_badge')}
            </span>
          )}
        </div>
      </div>

      <div className="p-8 space-y-8">
        {errorBanner}

        <section data-testid="policy-mfa-section">
          <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">
            {t('admin.policy.mfa_section')}
          </h4>
          <p className="text-xs text-gray-500 dark:text-dark-muted mb-3">
            {t('admin.policy.mfa_help')}
          </p>
          <div className="flex flex-wrap gap-2" role="group" aria-label={t('admin.policy.mfa_roles_label')}>
            {POLICY_MFA_ROLES.map((role) => {
              const active = draft.mfa_required_roles.includes(role);
              return (
                <button
                  key={role}
                  type="button"
                  disabled={!canWrite}
                  aria-pressed={active}
                  data-testid={`policy-mfa-${role}`}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      mfa_required_roles: active
                        ? draft.mfa_required_roles.filter((r) => r !== role)
                        : [...draft.mfa_required_roles, role],
                    })
                  }
                  className={`px-4 py-2 rounded-xl text-sm font-bold border transition-colors disabled:opacity-60 ${
                    active
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted border-gray-200 dark:border-dark-border'
                  }`}
                >
                  {role}
                </button>
              );
            })}
            <span
              className="px-4 py-2 rounded-xl text-sm font-bold border border-green-200 dark:border-green-900/40 bg-green-50 dark:bg-green-900/10 text-green-700 dark:text-green-400"
              title={t('admin.policy.mfa_system_admin_help')}
              data-testid="policy-mfa-system-admin"
            >
              SYSTEM_ADMIN · {t('admin.policy.mfa_always_on')}
            </span>
          </div>
        </section>

        <section className="grid gap-6 md:grid-cols-3">
          {numberField(
            'session_idle_minutes',
            t('admin.policy.idle_label'),
            t('admin.policy.idle_help'),
            5,
            10080,
          )}
          {numberField(
            'session_max_hours',
            t('admin.policy.max_age_label'),
            t('admin.policy.max_age_help'),
            1,
            8760,
          )}
          {numberField(
            'concurrent_session_limit',
            t('admin.policy.concurrent_label'),
            t('admin.policy.concurrent_help'),
            1,
            100,
          )}
        </section>

        <section data-testid="policy-ip-allowlist">
          <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">
            {t('admin.policy.ip_section')}
          </h4>
          <p className="text-xs text-gray-500 dark:text-dark-muted mb-3">
            {t('admin.policy.ip_help')}
          </p>
          {draft.ip_allowlist.length > 0 && (
            <ul className="mb-3 space-y-2" data-testid="policy-ip-list">
              {draft.ip_allowlist.map((entry) => (
                <li key={entry} className="flex items-center justify-between gap-3">
                  <code className="px-3 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-xs font-mono">
                    {entry}
                  </code>
                  <button
                    type="button"
                    disabled={!canWrite}
                    onClick={() => requestRemoveCidr(entry)}
                    className="p-1.5 text-gray-400 hover:text-red-600 rounded-lg disabled:opacity-50"
                    aria-label={t('admin.policy.ip_remove', { cidr: entry })}
                    data-testid={`policy-ip-remove-${entry}`}
                  >
                    <X className="w-4 h-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {draft.ip_allowlist.length === 0 && (
            <p className="text-xs text-gray-400 mb-3" data-testid="policy-ip-empty">
              {t('admin.policy.ip_empty')}
            </p>
          )}
          {canWrite && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={cidrInput}
                disabled={!canWrite}
                onChange={(e) => {
                  setCidrInput(e.target.value);
                  setCidrError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addCidr();
                  }
                }}
                placeholder="203.0.113.0/24"
                aria-label={t('admin.policy.ip_add_label')}
                data-testid="policy-ip-input"
                className="flex-1 max-w-xs px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg text-sm font-mono dark:bg-dark-border dark:text-dark-text"
              />
              <button
                type="button"
                onClick={addCidr}
                className="inline-flex items-center gap-1 px-3 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-bg"
                data-testid="policy-ip-add"
              >
                <Plus className="w-4 h-4" />
                {t('admin.policy.ip_add')}
              </button>
            </div>
          )}
          {cidrError && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{cidrError}</p>}
          {fieldError('ip_allowlist') && (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">{fieldError('ip_allowlist')}</p>
          )}
        </section>

        <section className="grid gap-6 md:grid-cols-2">
          <div data-testid="policy-field-patient_sharing">
            <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">
              {t('admin.policy.share_section')}
            </h4>
            <p className="text-xs text-gray-500 dark:text-dark-muted mb-3">
              {t('admin.policy.share_help')}
            </p>
            <div className="flex gap-2">
              {([true, false] as const).map((allowed) => (
                <button
                  key={String(allowed)}
                  type="button"
                  disabled={!canWrite}
                  aria-pressed={draft.patient_sharing === allowed}
                  data-testid={`policy-share-${allowed ? 'on' : 'off'}`}
                  onClick={() => setDraft({ ...draft, patient_sharing: allowed })}
                  className={`px-4 py-2 rounded-xl text-sm font-bold border transition-colors disabled:opacity-60 ${
                    draft.patient_sharing === allowed
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted border-gray-200 dark:border-dark-border'
                  }`}
                >
                  {allowed
                    ? t('access.allow_share_on')
                    : t('access.allow_share_off')}
                </button>
              ))}
            </div>
          </div>

          <div data-testid="policy-field-manager_scope">
            <h4 className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">
              {t('access.manager_scope_label')}
            </h4>
            <p className="text-xs text-gray-500 dark:text-dark-muted mb-3">
              {t('access.manager_scope_help')}
            </p>
            <div className="flex gap-2">
              {(['tenant', 'assigned'] as const).map((scope) => (
                <button
                  key={scope}
                  type="button"
                  disabled={!canWrite}
                  aria-pressed={draft.manager_scope === scope}
                  data-testid={`policy-scope-${scope}`}
                  onClick={() => setDraft({ ...draft, manager_scope: scope })}
                  className={`px-4 py-2 rounded-xl text-sm font-bold border transition-colors disabled:opacity-60 ${
                    draft.manager_scope === scope
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted border-gray-200 dark:border-dark-border'
                  }`}
                >
                  {scope === 'tenant'
                    ? t('access.manager_scope_tenant')
                    : t('access.manager_scope_assigned')}
                </button>
              ))}
            </div>
          </div>
        </section>

        <footer className="flex items-center justify-between border-t border-gray-100 dark:border-dark-border pt-4 text-xs text-gray-400">
          <span>
            {t('admin.policy.updated_at', {
              date: policy.updated_at ? formatTimestamp(new Date(policy.updated_at)) : '—',
            })}
          </span>
          <Link
            to="/admin/audit?action=tenant.security_policy"
            className="inline-flex items-center gap-1.5 text-blue-600 hover:text-blue-700 font-bold"
            data-testid="policy-audit-link"
          >
            <ScrollText className="w-3.5 h-3.5" />
            {t('access.audit_link')}
          </Link>
        </footer>
      </div>
    </div>
  );
}

/**
 * The tenant-level Security policy page (admin tenant settings shell).
 * Gated `settings:read`; the editor writes through `settings:write`
 * (ADMIN/SYSTEM_ADMIN — MANAGER gets the read-only view).
 */
export default function TenantSecurityPolicy() {
  const { t } = useTranslation();
  const tenant = useAccessStore((s) => s.tenant);
  const can = useAccessStore((s) => s.can);
  const canWrite = can('settings:write');

  const loadFallback = useCallback(() => <div className="text-sm text-gray-400" />, []);

  if (!tenant) return loadFallback();

  return (
    <div className="space-y-8 pb-10">
      <PageHeader
        title={t('admin.policy.page_title')}
        subtitle={t('admin.policy.page_subtitle')}
        icon={<KeyRound className="w-8 h-8" />}
      />
      <TenantSecurityPolicyCard tenantId={tenant.id} canWrite={canWrite} />
    </div>
  );
}
