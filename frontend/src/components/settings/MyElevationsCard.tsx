import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, ShieldAlert } from 'lucide-react';
import { useAccessStore } from '../../store/slices/accessSlice';
import { apiErrorDetail } from '../../utils/apiErrors';
import { listMyElevations, type Elevation } from '../../services/elevationService';

function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

/**
 * The caller's own break-glass ledger (`GET /me/elevations`) — pending,
 * live and settled windows they requested. Only rendered for callers who
 * may request an elevation at all.
 */
export function MyElevationsCard() {
  const { t } = useTranslation();
  const tRef = useRef(t);
  tRef.current = t;
  const canRequest = useAccessStore((s) => s.can('break_glass:request'));
  const [rows, setRows] = useState<Elevation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!canRequest) return;
    let cancelled = false;
    (async () => {
      try {
        const items = await listMyElevations();
        if (!cancelled) setRows(items);
      } catch (err) {
        if (!cancelled) {
          setError(apiErrorDetail(err) || tRef.current('elevations.my_load_failed', 'Could not load your elevations.'));
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canRequest]);

  if (!canRequest) return null;

  return (
    <div
      className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-6 space-y-4"
      data-testid="my-elevations-card"
    >
      <div className="flex items-center space-x-3">
        <ShieldAlert className="w-4 h-4 text-gray-400" />
        <div>
          <p className="text-sm font-medium text-gray-900 dark:text-dark-text">
            {t('elevations.my_title', 'Your access elevations')}
          </p>
          <p className="text-sm text-gray-500 dark:text-dark-muted">
            {t('elevations.my_desc', 'Break-glass windows you requested, including pending and settled ones.')}
          </p>
        </div>
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {rows === null ? (
        <div className="flex items-center justify-center py-4 text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
        </div>
      ) : rows.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-dark-muted" data-testid="my-elevations-empty">
          {t('elevations.my_empty', 'You have not requested any elevations.')}
        </p>
      ) : (
        <ul className="divide-y divide-gray-100 dark:divide-dark-border" data-testid="my-elevations-list">
          {rows.map((row) => (
            <li key={row.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wide bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted">
                  {t(`elevations.status_${row.status}`, row.status)}
                </span>
                <span className="text-[10px] font-black uppercase tracking-wide text-gray-400">
                  {row.scope === 'system'
                    ? t('elevations.scope_system', 'Instance')
                    : t('elevations.scope_tenant', 'Tenant')}
                </span>
              </div>
              <p className="text-sm text-gray-900 dark:text-dark-text mt-1">{row.reason}</p>
              <p className="text-xs text-gray-500 dark:text-dark-muted">
                {t('elevations.requested_at', 'Requested')}: {formatDate(row.requested_at)} ·{' '}
                {t('elevations.expires_at', 'Expires')}: {formatDate(row.expires_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
