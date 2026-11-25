import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { CheckCircle2, Loader2, Plus, ShieldAlert, ShieldCheck, XCircle } from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';
import { Modal } from '../../components/ui/Modal';
import { useAccessStore } from '../../store/slices/accessSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import {
  approveElevation,
  denyElevation,
  listElevations,
  requestElevation,
  revokeElevation,
  type Elevation,
  type ElevationScope,
  type ElevationStatus,
} from '../../services/elevationService';

import { formatTimestamp } from '../../lib/dateFormats';
const STATUS_FILTERS: Array<'all' | ElevationStatus> = [
  'all',
  'pending',
  'active',
  'expired',
  'revoked',
  'denied',
  'cancelled',
];

function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : formatTimestamp(date);
}

function statusTone(status: ElevationStatus): string {
  switch (status) {
    case 'active':
      return 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400';
    case 'pending':
      return 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400';
    case 'revoked':
    case 'denied':
      return 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400';
    default:
      return 'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted';
  }
}

interface ElevationQueueProps {
  scope: ElevationScope;
}

/**
 * Break-glass elevation queue (program 12A UI).
 *
 * `scope="tenant"` lists the tenant ledger (a `MANAGER` may request, an
 * `ADMIN` may approve a second person's request); `scope="system"` lists
 * instance-level windows and is reachable by SYSTEM_ADMIN only. Approval
 * is always a second person — the requester's own row never shows the
 * decision buttons.
 */
export function ElevationQueue({ scope }: ElevationQueueProps) {
  const { t } = useTranslation();
  const tRef = useRef(t);
  tRef.current = t;
  const tenant = useAccessStore((s) => s.tenant);
  const permissions = useAccessStore((s) => s.permissions);
  const can = useAccessStore((s) => s.can);
  const callerId = useAuthStore((s) => s.user?.id ?? null);
  const canRequest = can('break_glass:request');
  const canApprove = can('break_glass:approve');

  const [rows, setRows] = useState<Elevation[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<'all' | ElevationStatus>('pending');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showRequest, setShowRequest] = useState(false);
  const [reason, setReason] = useState('');
  const [duration, setDuration] = useState(30);
  const [selected, setSelected] = useState<string[]>([]);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);

  const heldPermissions = useMemo(
    () => Array.from(permissions).sort((a, b) => a.localeCompare(b)),
    [permissions],
  );

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const result = await listElevations({
        scope,
        tenantId: scope === 'tenant' ? tenant?.id : undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
      });
      setRows(result.items ?? []);
    } catch (err) {
      setLoadError(apiErrorDetail(err) || tRef.current('elevations.load_failed', 'Could not load the elevation queue.'));
      setRows([]);
    }
  }, [scope, tenant?.id, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const openRequest = () => {
    setReason('');
    setDuration(30);
    setSelected([]);
    setRequestError(null);
    setShowRequest(true);
  };

  const submitRequest = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!reason.trim() || selected.length === 0) return;
    setRequesting(true);
    setRequestError(null);
    try {
      await requestElevation({
        scope,
        permissions: selected,
        duration_minutes: duration,
        reason: reason.trim(),
      });
      setShowRequest(false);
      toast.success(t('elevations.requested_toast', 'Elevation requested — a second person must approve it.'));
      await load();
    } catch (err) {
      setRequestError(apiErrorDetail(err) || t('elevations.request_failed', 'Could not request the elevation.'));
    } finally {
      setRequesting(false);
    }
  };

  const decide = async (
    row: Elevation,
    action: 'approve' | 'deny' | 'revoke',
  ) => {
    setBusy(`${row.id}:${action}`);
    try {
      if (action === 'approve') await approveElevation(row.id);
      if (action === 'deny') await denyElevation(row.id);
      if (action === 'revoke') await revokeElevation(row.id);
      toast.success(t(`elevations.${action}_toast`, `${action} recorded.`));
      await load();
    } catch (err) {
      toast.error(apiErrorDetail(err) || t('elevations.action_failed', 'Could not update the elevation.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-8 pb-10">
      <PageHeader
        title={t('elevations.title', 'Break-glass elevations')}
        subtitle={
          scope === 'system'
            ? t('elevations.subtitle_system', 'Instance-level emergency access windows, with two-person approval.')
            : t('elevations.subtitle_tenant', 'Emergency access windows for this tenant, with two-person approval.')
        }
        icon={<ShieldAlert className="w-8 h-8" />}
        actions={
          canRequest ? (
            <button
              type="button"
              onClick={openRequest}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold"
              data-testid="elevation-request-open"
            >
              <Plus className="w-4 h-4" />
              {t('elevations.request', 'Request elevation')}
            </button>
          ) : undefined
        }
      />

      <div className="flex flex-wrap items-center gap-2" data-testid="elevation-filters">
        {STATUS_FILTERS.map((status) => (
          <button
            key={status}
            type="button"
            aria-pressed={statusFilter === status}
            onClick={() => setStatusFilter(status)}
            className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-colors ${
              statusFilter === status
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-muted border-gray-200 dark:border-dark-border'
            }`}
            data-testid={`elevation-filter-${status}`}
          >
            {t(`elevations.status_${status}`, status)}
          </button>
        ))}
      </div>

      {loadError && (
        <p className="text-sm text-red-600 dark:text-red-400" data-testid="elevation-load-error">{loadError}</p>
      )}

      {rows === null ? (
        <div className="flex items-center justify-center py-10 text-gray-400">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : rows.length === 0 ? (
        <div
          className="bg-white dark:bg-dark-surface rounded-3xl border border-gray-100 dark:border-dark-border p-10 text-center text-sm text-gray-500 dark:text-dark-muted"
          data-testid="elevation-empty"
        >
          {t('elevations.empty', 'No elevations match this filter.')}
        </div>
      ) : (
        <ul className="space-y-3">
          {rows.map((row) => {
            const own = callerId !== null && row.requested_by === callerId;
            const deciding = busy?.startsWith(`${row.id}:`) ?? false;
            return (
              <li
                key={row.id}
                className="bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border p-5 space-y-3"
                data-testid={`elevation-row-${row.id}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide ${statusTone(row.status)}`}>
                        {t(`elevations.status_${row.status}`, row.status)}
                      </span>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400">
                        {row.scope === 'system'
                          ? t('elevations.scope_system', 'Instance')
                          : t('elevations.scope_tenant', 'Tenant')}
                      </span>
                      {own && (
                        <span className="text-[10px] font-black uppercase tracking-wide text-gray-400">
                          {t('elevations.own', 'Your request')}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-900 dark:text-dark-text">
                      {row.reason}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-dark-muted">
                      {t('elevations.requested_at', 'Requested')}: {formatDate(row.requested_at)} ·{' '}
                      {t('elevations.expires_at', 'Expires')}: {formatDate(row.expires_at)} ·{' '}
                      {t('elevations.duration', 'Duration')}: {row.duration_minutes} min
                    </p>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {row.permissions.map((permission) => (
                        <code key={permission} className="px-2 py-0.5 rounded-md bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border text-[10px] font-mono text-gray-600 dark:text-dark-muted">
                          {permission}
                        </code>
                      ))}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {row.status === 'pending' && canApprove && !own && (
                      <>
                        <button
                          type="button"
                          onClick={() => decide(row, 'approve')}
                          disabled={deciding}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
                          data-testid={`elevation-approve-${row.id}`}
                        >
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          {t('elevations.approve', 'Approve')}
                        </button>
                        <button
                          type="button"
                          onClick={() => decide(row, 'deny')}
                          disabled={deciding}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg border border-red-200 text-red-600 hover:bg-red-50 dark:border-red-900/40 dark:text-red-400 dark:hover:bg-red-900/20 disabled:opacity-50"
                          data-testid={`elevation-deny-${row.id}`}
                        >
                          <XCircle className="w-3.5 h-3.5" />
                          {t('elevations.deny', 'Deny')}
                        </button>
                      </>
                    )}
                    {(row.status === 'pending' || row.status === 'active') &&
                      (own || canApprove) && (
                        <button
                          type="button"
                          onClick={() => decide(row, 'revoke')}
                          disabled={deciding}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-dark-border dark:text-dark-muted dark:hover:bg-dark-border disabled:opacity-50"
                          data-testid={`elevation-revoke-${row.id}`}
                        >
                          <ShieldCheck className="w-3.5 h-3.5" />
                          {t('elevations.revoke', 'Revoke')}
                        </button>
                      )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <Modal
        open={showRequest}
        onOpenChange={(open) => setShowRequest(open)}
        title={t('elevations.request_title', 'Request break-glass access')}
        className="max-w-xl"
      >
        <form onSubmit={submitRequest} className="p-6 space-y-4" data-testid="elevation-request-form">
          <p className="text-sm text-gray-500 dark:text-dark-muted">
            {t('elevations.request_hint', 'An elevation widens only permissions you already hold, for a bounded window. A second person must approve it.')}
          </p>
          <div>
            <p className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              {t('elevations.permissions', 'Permissions')}
            </p>
            <div className="max-h-52 overflow-y-auto rounded-xl border border-gray-200 dark:border-dark-border p-3 space-y-1.5">
              {heldPermissions.length === 0 && (
                <p className="text-xs text-gray-400">{t('elevations.no_permissions', 'No permissions are available.')}</p>
              )}
              {heldPermissions.map((permission) => (
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
                    data-testid={`elevation-permission-${permission}`}
                  />
                  <code className="font-mono">{permission}</code>
                </label>
              ))}
            </div>
          </div>
          <div>
            <label htmlFor="elevation-duration" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('elevations.duration_label', 'Duration (minutes)')}
            </label>
            <input
              id="elevation-duration"
              type="number"
              min={1}
              max={240}
              required
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              className="w-40 px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
            />
          </div>
          <div>
            <label htmlFor="elevation-reason" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-1">
              {t('elevations.reason', 'Reason')}
            </label>
            <textarea
              id="elevation-reason"
              required
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              placeholder={t('elevations.reason_placeholder', 'Why is emergency access needed?')}
            />
          </div>
          {requestError && <p className="text-sm text-red-600 dark:text-red-400">{requestError}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowRequest(false)}
              className="px-4 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold text-gray-600 dark:text-dark-muted"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              type="submit"
              disabled={requesting || !reason.trim() || selected.length === 0}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold disabled:opacity-50"
            >
              {requesting && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('elevations.request_submit', 'Request')}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

export default ElevationQueue;
