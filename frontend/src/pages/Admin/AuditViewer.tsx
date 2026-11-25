import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { toast } from 'react-toastify';
import {
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  ScrollText,
  ShieldCheck,
} from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { useAccessStore } from '../../store/slices/accessSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import { listTenants, type Tenant } from '../../services/tenantService';
import {
  exportAuditEvents,
  getAuditRetention,
  listAuditEvents,
  verifyAuditChain,
  type AuditChainStatus,
  type AuditEvent,
  type AuditFilters,
  type AuditRetentionResponse,
} from '../../services/adminAuditService';

import { formatTimestamp } from '../../lib/dateFormats';
const OUTCOMES = ['ok', 'denied', 'error'] as const;
const PAGE_SIZES = [25, 50, 100, 250];

function shortHash(hash: string | null | undefined): string {
  if (!hash) return '—';
  return hash.length > 10 ? `${hash.slice(0, 10)}…` : hash;
}

function shortId(id: string | null | undefined): string {
  if (!id) return '—';
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : formatTimestamp(date);
}

/** True when a diff value is the write-time redaction marker (no PHI ever
 * reaches the viewer — the backend masked it before storing). */
function isRedacted(value: unknown): boolean {
  return value === '[redacted]';
}

function DiffPayload({ title, payload }: { title: string; payload: Record<string, unknown> | null | undefined }) {
  const { t } = useTranslation();
  if (!payload || Object.keys(payload).length === 0) return null;
  return (
    <div className="min-w-0">
      <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">{title}</p>
      <ul className="space-y-1" data-testid={`audit-diff-${title}`}>
        {Object.entries(payload).map(([key, value]) => (
          <li key={key} className="text-xs font-mono break-all">
            <span className="text-gray-500 dark:text-dark-muted">{key}: </span>
            {isRedacted(value) ? (
              <span className="text-amber-700 dark:text-amber-400 italic" title={String(value)}>
                {t('admin.audit.redacted')}
              </span>
            ) : (
              <span className="text-gray-800 dark:text-dark-text">{JSON.stringify(value)}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RowHashCell({ label, value }: { label: string; value: string | null | undefined }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  if (!value) return <span>—</span>;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // insecure context — the short hash stays visible as text
    }
  };
  return (
    <span className="inline-flex items-center gap-1" data-testid={`audit-${label}`}>
      <code className="text-xs font-mono">{shortHash(value)}</code>
      <button
        type="button"
        onClick={copy}
        className="p-0.5 text-gray-400 hover:text-blue-600"
        aria-label={t('admin.audit.copy_hash', { label })}
        title={t('admin.audit.copy_hash', { label })}
      >
        {copied ? <ShieldCheck className="w-3 h-3 text-green-600" /> : <Copy className="w-3 h-3" />}
      </button>
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const { t } = useTranslation();
  const tone =
    outcome === 'ok'
      ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
      : outcome === 'denied'
        ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400'
        : 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400';
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide ${tone}`}>
      {t(`admin.audit.outcome_${outcome}`, outcome)}
    </span>
  );
}

function EventRow({ event }: { event: AuditEvent }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const hasDiff = Boolean(
    (event.old_value && Object.keys(event.old_value).length > 0) || // # gate-allow: *, audit diff
      (event.new_value && Object.keys(event.new_value).length > 0),
  );
  return (
    <>
      <tr className="border-b border-gray-50 dark:border-dark-border/60 hover:bg-gray-50/50 dark:hover:bg-dark-bg/40" data-testid="audit-row">
        <td className="px-3 py-2 text-xs text-gray-600 dark:text-dark-muted whitespace-nowrap">
          {formatDateTime(event.created_at)}
        </td>
        <td className="px-3 py-2 text-xs font-mono" title={event.user_id ?? ''}>
          {shortId(event.user_id)}
        </td>
        <td className="px-3 py-2 text-xs font-bold text-gray-800 dark:text-dark-text">{event.action}</td>
        <td className="px-3 py-2 text-xs text-gray-600 dark:text-dark-muted">
          {event.resource_type}
          {event.resource_id && (
            <span className="ml-1 font-mono text-gray-400" title={event.resource_id}>
              {shortId(event.resource_id)}
            </span>
          )}
        </td>
        <td className="px-3 py-2">
          <OutcomeBadge outcome={event.outcome} />
        </td>
        <td className="px-3 py-2 text-xs text-gray-500 dark:text-dark-muted">
          {event.seq ?? '—'}
          {!event.tenant_id && (
            <span className="ml-1 text-[10px] uppercase text-gray-400">{t('admin.audit.system_row')}</span>
          )}
        </td>
        <td className="px-3 py-2">
          <RowHashCell label="entry_hash" value={event.entry_hash} />
        </td>
        <td className="px-3 py-2 text-right">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="p-1 text-gray-400 hover:text-blue-600"
            aria-label={t('admin.audit.toggle_row')}
            aria-expanded={expanded}
            data-testid="audit-row-toggle"
          >
            {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-gray-50 dark:border-dark-border/60 bg-gray-50/60 dark:bg-dark-bg/60">
          <td colSpan={8} className="px-6 py-4">
            <div className="grid gap-6 md:grid-cols-2">
              <DiffPayload title="old_value" payload={event.old_value} /> {/* # gate-allow: *, audit diff */}
              <DiffPayload title="new_value" payload={event.new_value} />
            </div>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-gray-400 font-mono">
              <span data-testid="audit-chain-key">
                {t('admin.audit.chain_key')}: {event.chain_key ?? '—'}
              </span>
              <span>
                {t('admin.audit.prev_hash')}: {shortHash(event.prev_hash)}
              </span>
              {!hasDiff && <span>{t('admin.audit.no_diff')}</span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function VerifyBanner({ chains }: { chains: AuditChainStatus[] }) {
  const { t } = useTranslation();
  const allOk = chains.every((chain) => chain.ok);
  return (
    <div
      className={`rounded-xl border px-4 py-3 text-sm space-y-1 ${
        allOk
          ? 'border-green-200 dark:border-green-900/40 bg-green-50 dark:bg-green-900/10 text-green-700 dark:text-green-400'
          : 'border-red-200 dark:border-red-900/40 bg-red-50 dark:bg-red-900/10 text-red-700 dark:text-red-400'
      }`}
      data-testid="audit-verify-banner"
    >
      <p className="font-bold">
        {allOk ? t('admin.audit.verify_ok') : t('admin.audit.verify_failed')}
      </p>
      {chains.map((chain) => (
        <p key={chain.chain_key} className="text-xs font-mono">
          {chain.chain_key}: {chain.entries} · {t(`admin.audit.reason_${chain.reason}`, chain.reason)}
          {chain.first_bad_seq != null && ` · ${t('admin.audit.first_bad_seq')} ${chain.first_bad_seq}`}
        </p>
      ))}
    </div>
  );
}

function RetentionCard() {
  const { t } = useTranslation();
  const [retention, setRetention] = useState<AuditRetentionResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getAuditRetention();
        if (!cancelled) setRetention(data);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) return null;
  if (!retention) return <LoadingState variant="inline" showText={false} message="" />;

  return (
    <div
      className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-3"
      data-testid="audit-retention-card"
    >
      <h3 className="text-sm font-bold text-brand-navy dark:text-dark-text">
        {t('admin.audit.retention_title')}
      </h3>
      <ul className="space-y-1">
        {retention.policies.map((policy) => (
          <li key={`${policy.scope}:${policy.scope_value}`} className="text-xs text-gray-600 dark:text-dark-muted">
            <code className="font-mono">{policy.scope_value}</code> · {policy.action} · {policy.days}d
            {!policy.enabled && <span className="ml-1 text-gray-400">({t('admin.audit.retention_disabled')})</span>}
          </li>
        ))}
      </ul>
      <p className="text-xs text-gray-400">{retention.notes}</p>
      <p className="text-xs text-gray-400" data-testid="audit-legal-holds">
        {t('admin.audit.legal_holds_null')}
      </p>
    </div>
  );
}

/**
 * The admin audit viewer (ADR 0054 Phase 3 — 8C). ADMIN: own tenant only
 * (the server enforces it; the picker/all toggle stays hidden). SYSTEM_ADMIN:
 * the full stream plus the system chain, with a tenant picker, `all` toggle,
 * chain verification and the retention view. Gated `audit:read`.
 */
export default function AuditViewer() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const role = useAccessStore((s) => s.roles[0]);
  const isSystemAdmin = role === 'SYSTEM_ADMIN';

  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantFilter, setTenantFilter] = useState<string>('');
  const [allTenants, setAllTenants] = useState(false);
  const [userId, setUserId] = useState('');
  const [resourceType, setResourceType] = useState(searchParams.get('resource_type') ?? '');
  const [resourceId, setResourceId] = useState('');
  const [action, setAction] = useState(searchParams.get('action') ?? '');
  const [outcome, setOutcome] = useState<string>('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [verifyChains, setVerifyChains] = useState<AuditChainStatus[] | null>(null);
  const [verifying, setVerifying] = useState(false);

  // A policy change audited elsewhere deep-links here with ?action=…
  useEffect(() => {
    const actionParam = searchParams.get('action');
    if (actionParam && actionParam !== action) {
      setAction(actionParam);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  useEffect(() => {
    if (!isSystemAdmin) return;
    let cancelled = false;
    listTenants({ limit: 250 })
      .then((page) => {
        if (!cancelled) setTenants(page.items);
      })
      .catch(() => {
        // The picker degrades to the default stream when the directory
        // cannot load (the viewer itself still works).
      });
    return () => {
      cancelled = true;
    };
  }, [isSystemAdmin]);

  const filters = useMemo(
    (): AuditFilters => ({
      tenant_id: isSystemAdmin && !allTenants && tenantFilter ? tenantFilter : null,
      all: isSystemAdmin ? allTenants : undefined,
      user_id: userId.trim() || null,
      resource_type: resourceType.trim() || null,
      resource_id: resourceId.trim() || null,
      action: action.trim() || null,
      outcome: (outcome as AuditFilters['outcome']) || null,
      date_from: dateFrom || null,
      date_to: dateTo ? `${dateTo}T23:59:59.999` : null,
      limit,
      offset,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isSystemAdmin, allTenants, tenantFilter, userId, resourceType, resourceId, action, outcome, dateFrom, dateTo, limit, offset],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listAuditEvents(filters);
      setEvents(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(apiErrorDetail(err) || t('admin.audit.load_failed'));
      setEvents([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
    // `t` is stable enough for this surface; filters is the memoized input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  const runExport = async (format: 'json' | 'csv') => {
    setExporting(true);
    try {
      const { truncated, totalMatched } = await exportAuditEvents(filters, format);
      if (truncated) {
        toast.warning(
          totalMatched == null
            ? t('admin.audit.export_truncated_unknown')
            : t('admin.audit.export_truncated', { count: totalMatched }),
        );
      }
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('admin.audit.export_failed'));
    } finally {
      setExporting(false);
    }
  };

  const runVerify = async () => {
    setVerifying(true);
    setVerifyChains(null);
    try {
      const response = await verifyAuditChain({
        tenantId: isSystemAdmin && !allTenants && tenantFilter ? tenantFilter : null,
        all: isSystemAdmin ? allTenants : false,
      });
      setVerifyChains('chains' in response ? response.chains : [response.chain]);
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('admin.audit.verify_failed'));
    } finally {
      setVerifying(false);
    }
  };

  const clearActionLink = () => {
    setSearchParams({}, { replace: true });
  };

  const inputClass =
    'w-full px-3 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-sm dark:bg-dark-border dark:text-dark-text';

  return (
    <div className="space-y-6 pb-10">
      <PageHeader
        title={t('admin.audit.title')}
        subtitle={t('admin.audit.subtitle')}
        icon={<ScrollText className="w-8 h-8" />}
      />

      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-4" data-testid="audit-filters">
        <div className="grid gap-4 md:grid-cols-4">
          {isSystemAdmin && (
            <>
              <div>
                <label htmlFor="audit-tenant" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
                  {t('admin.audit.filter_tenant')}
                </label>
                <select
                  id="audit-tenant"
                  value={allTenants ? '__all__' : tenantFilter}
                  onChange={(e) => {
                    setOffset(0);
                    setVerifyChains(null);
                    if (e.target.value === '__all__') {
                      setAllTenants(true);
                      setTenantFilter('');
                    } else {
                      setAllTenants(false);
                      setTenantFilter(e.target.value);
                    }
                  }}
                  className={inputClass}
                  data-testid="audit-tenant-picker"
                >
                  <option value="">{t('admin.audit.filter_system_chain')}</option>
                  <option value="__all__">{t('admin.audit.filter_all')}</option>
                  {tenants.map((tenantOption) => (
                    <option key={tenantOption.id} value={tenantOption.id}>
                      {tenantOption.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="md:col-span-3 flex items-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setOffset(0);
                    setVerifyChains(null);
                    setAllTenants((v) => !v);
                  }}
                  aria-pressed={allTenants}
                  data-testid="audit-all-toggle"
                  className={`px-4 py-2 rounded-xl text-sm font-bold border transition-colors ${
                    allTenants
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted border-gray-200 dark:border-dark-border'
                  }`}
                >
                  {t('admin.audit.all_toggle')}
                </button>
              </div>
            </>
          )}
          <div>
            <label htmlFor="audit-user" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
              {t('admin.audit.filter_actor')}
            </label>
            <input
              id="audit-user"
              type="text"
              value={userId}
              onChange={(e) => {
                setOffset(0);
                setUserId(e.target.value);
              }}
              placeholder={t('admin.audit.filter_actor_ph')}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="audit-resource-type" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
              {t('admin.audit.filter_resource_type')}
            </label>
            <input
              id="audit-resource-type"
              type="text"
              value={resourceType}
              onChange={(e) => {
                setOffset(0);
                setResourceType(e.target.value);
                if (searchParams.get('resource_type')) clearActionLink();
              }}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="audit-resource-id" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
              {t('admin.audit.filter_resource_id')}
            </label>
            <input
              id="audit-resource-id"
              type="text"
              value={resourceId}
              onChange={(e) => {
                setOffset(0);
                setResourceId(e.target.value);
              }}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="audit-action" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
              {t('admin.audit.filter_action')}
            </label>
            <input
              id="audit-action"
              type="text"
              value={action}
              onChange={(e) => {
                setOffset(0);
                setAction(e.target.value);
                if (searchParams.get('action')) clearActionLink();
              }}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="audit-outcome" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
              {t('admin.audit.filter_outcome')}
            </label>
            <select
              id="audit-outcome"
              value={outcome}
              onChange={(e) => {
                setOffset(0);
                setOutcome(e.target.value);
              }}
              className={inputClass}
              data-testid="audit-outcome-filter"
            >
              <option value="">{t('admin.audit.outcome_any')}</option>
              {OUTCOMES.map((value) => (
                <option key={value} value={value}>
                  {t(`admin.audit.outcome_${value}`, value)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="audit-date-from" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
              {t('admin.audit.filter_from')}
            </label>
            <input
              id="audit-date-from"
              type="date"
              value={dateFrom}
              onChange={(e) => {
                setOffset(0);
                setDateFrom(e.target.value);
              }}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor="audit-date-to" className="block text-xs font-bold text-gray-500 dark:text-dark-muted mb-1">
              {t('admin.audit.filter_to')}
            </label>
            <input
              id="audit-date-to"
              type="date"
              value={dateTo}
              onChange={(e) => {
                setOffset(0);
                setDateTo(e.target.value);
              }}
              className={inputClass}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-gray-100 dark:border-dark-border pt-4">
          <button
            type="button"
            onClick={() => runExport('json')}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 px-3 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-xs font-bold text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-bg disabled:opacity-50"
            data-testid="audit-export-json"
          >
            <Download className="w-3.5 h-3.5" />
            {t('admin.audit.export_json')}
          </button>
          <button
            type="button"
            onClick={() => runExport('csv')}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 px-3 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-xs font-bold text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-bg disabled:opacity-50"
            data-testid="audit-export-csv"
          >
            <Download className="w-3.5 h-3.5" />
            {t('admin.audit.export_csv')}
          </button>
          {isSystemAdmin && (
            <button
              type="button"
              onClick={runVerify}
              disabled={verifying}
              className="inline-flex items-center gap-1.5 px-3 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-xs font-bold text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-bg disabled:opacity-50"
              data-testid="audit-verify"
            >
              <ShieldCheck className="w-3.5 h-3.5" />
              {verifying ? t('admin.audit.verifying') : t('admin.audit.verify')}
            </button>
          )}
          <span className="ml-auto text-xs text-gray-400" data-testid="audit-total">
            {t('admin.audit.total', { count: total })}
          </span>
        </div>

        {verifyChains && <VerifyBanner chains={verifyChains} />}
      </div>

      {error && (
        <div
          className="rounded-xl border border-red-200 dark:border-red-900/40 bg-red-50 dark:bg-red-900/10 px-4 py-3 text-sm text-red-700 dark:text-red-400"
          data-testid="audit-error"
          role="alert"
        >
          {error}
        </div>
      )}

      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border overflow-hidden" data-testid="audit-table">
        {loading ? (
          <LoadingState variant="inline" showText={false} message="" />
        ) : events.length === 0 ? (
          <p className="px-6 py-10 text-center text-sm text-gray-400" data-testid="audit-empty">
            {t('admin.audit.empty')}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/30">
                  {(
                    [
                      'col_time',
                      'col_actor',
                      'col_action',
                      'col_resource',
                      'col_outcome',
                      'col_seq',
                      'col_hash',
                    ] as const
                  ).map((key) => (
                    <th
                      key={key}
                      className="px-3 py-2 text-[10px] font-black uppercase tracking-widest text-gray-400"
                    >
                      {t(`admin.audit.${key}`)}
                    </th>
                  ))}
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between" data-testid="audit-pagination">
        <label htmlFor="audit-limit" className="text-xs text-gray-400">
          {t('admin.audit.page_size')}
          <select
            id="audit-limit"
            value={limit}
            onChange={(e) => {
              setOffset(0);
              setLimit(Number(e.target.value));
            }}
            className="ml-2 px-2 py-1 border border-gray-200 dark:border-dark-border rounded-lg text-xs dark:bg-dark-border dark:text-dark-text"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setOffset((v) => Math.max(0, v - limit))}
            disabled={offset === 0 || loading}
            className="px-3 py-1.5 border border-gray-200 dark:border-dark-border rounded-lg text-xs font-bold text-gray-600 dark:text-dark-muted disabled:opacity-40"
            data-testid="audit-prev"
          >
            ← {t('admin.audit.prev')}
          </button>
          <span className="text-xs text-gray-400">
            {total === 0 ? 0 : offset + 1}–{Math.min(offset + limit, total)} / {total}
          </span>
          <button
            type="button"
            onClick={() => setOffset((v) => v + limit)}
            disabled={offset + limit >= total || loading}
            className="px-3 py-1.5 border border-gray-200 dark:border-dark-border rounded-lg text-xs font-bold text-gray-600 dark:text-dark-muted disabled:opacity-40"
            data-testid="audit-next"
          >
            {t('admin.audit.next')} →
          </button>
        </div>
      </div>

      {isSystemAdmin && <RetentionCard />}
    </div>
  );
}
