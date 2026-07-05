import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Megaphone,
  Send,
  RefreshCw,
  Activity,
  Users,
  Mail,
  ShieldAlert,
  X,
  CheckCircle2,
  XCircle,
  Clock,
  Inbox as InboxIcon,
} from 'lucide-react';
import { format, formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import {
  notificationService,
  type NotificationRule,
  type DeliveryDetail,
  type DeliveryStatus,
  type DeliveryChannel,
} from '../../services/notificationService';
import { useAuthStore } from '../../store/slices/authSlice';

interface AdminItem {
  id: string;
  title: string;
  body?: string | null;
  source: string;
  category: string;
  severity: string;
  type: string;
  tenant_id?: string | null;
  patient_id?: string | null;
  created_at?: string | null;
}

interface Stats {
  by_source: Record<string, number>;
  by_category: Record<string, number>;
  delivery: Record<string, Record<string, number>>;
  recipients: number;
  unique_recipients: number;
  total: number;
}

const SOURCE_COLORS: Record<string, string> = {
  SYSTEM: 'bg-gray-100 text-gray-700 dark:bg-dark-border dark:text-dark-text',
  INTEGRATION: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400',
  AGENT: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  RULE: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  CLINICAL: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  SCHEDULED: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
};

export function AdminNotificationCenter() {
  const { t, i18n } = useTranslation();
  const dateLocale: Locale = i18n.language === 'el' ? el : enUS;
  const user = useAuthStore((s) => s.user);
  const isSystemAdmin = user?.role === 'SYSTEM_ADMIN';

  const [items, setItems] = useState<AdminItem[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Broadcast composer state
  const [bcTitle, setBcTitle] = useState('');
  const [bcBody, setBcBody] = useState('');
  const [bcSeverity, setBcSeverity] = useState('info');
  const [bcScope, setBcScope] = useState<'tenant' | 'system'>('tenant');
  const [sending, setSending] = useState(false);

  // Delivery detail modal state
  const [detail, setDetail] = useState<DeliveryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const openDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    try {
      const d = await notificationService.getDeliveryDetail(id);
      setDetail(d);
    } catch (err: any) {
      setDetailError(err?.response?.data?.detail ?? err.message ?? 'Failed to load delivery detail');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const closeDetail = useCallback(() => {
    setDetail(null);
    setDetailError(null);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [feed, st] = await Promise.all([
        notificationService.getAdminFeed({ limit: 50 }).catch(() => ({ items: [], total: 0 })),
        notificationService.getAdminStats().catch(() => null),
      ]);
      setItems((feed.items ?? []) as AdminItem[]);
      setStats(st);
    } catch (err: any) {
      setError(err?.message ?? 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleBroadcast = async () => {
    if (!bcTitle.trim()) {
      setError(t('notifications.admin.title_required', { defaultValue: 'A title is required.' }));
      return;
    }
    setSending(true);
    setError(null);
    try {
      await notificationService.broadcast({
        title: bcTitle.trim(),
        body: bcBody.trim() || undefined,
        severity: bcSeverity,
        scope: bcScope,
      });
      setBcTitle('');
      setBcBody('');
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err.message ?? 'Broadcast failed');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          icon={<Megaphone className="w-4 h-4" />}
          label={t('notifications.admin.total', { defaultValue: 'Notifications' })}
          value={stats?.total}
          loading={loading}
        />
        <StatCard
          icon={<Users className="w-4 h-4" />}
          label={t('notifications.admin.recipients', { defaultValue: 'Recipients' })}
          value={stats?.unique_recipients}
          loading={loading}
        />
        <StatCard
          icon={<Activity className="w-4 h-4" />}
          label={t('notifications.admin.sources', { defaultValue: 'Sources' })}
          value={stats ? Object.keys(stats.by_source).length : undefined}
          loading={loading}
        />
        <StatCard
          icon={<Mail className="w-4 h-4" />}
          label={t('notifications.admin.delivered', { defaultValue: 'Delivered (push)' })}
          value={sumDelivered(stats)}
          loading={loading}
        />
      </div>

      {/* Broadcast composer */}
      <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl p-5 space-y-3">
        <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text flex items-center">
          <Megaphone className="w-4 h-4 mr-2 text-blue-600" />
          {t('notifications.admin.broadcast', { defaultValue: 'Broadcast a notice' })}
        </h3>
        <input
          value={bcTitle}
          onChange={(e) => setBcTitle(e.target.value)}
          placeholder={t('notifications.admin.title_placeholder', {
            defaultValue: 'Notice title (e.g. Scheduled maintenance)',
          })}
          className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text"
        />
        <textarea
          value={bcBody}
          onChange={(e) => setBcBody(e.target.value)}
          placeholder={t('notifications.admin.body_placeholder', { defaultValue: 'Optional message…' })}
          rows={2}
          className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text"
        />
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={bcSeverity}
            onChange={(e) => setBcSeverity(e.target.value)}
            className="text-xs border border-gray-200 dark:border-dark-border rounded-lg px-2 py-1.5 bg-white dark:bg-dark-surface dark:text-dark-text"
          >
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
          </select>
          <select
            value={bcScope}
            onChange={(e) => setBcScope(e.target.value as 'tenant' | 'system')}
            className="text-xs border border-gray-200 dark:border-dark-border rounded-lg px-2 py-1.5 bg-white dark:bg-dark-surface dark:text-dark-text"
            disabled={!isSystemAdmin}
            title={
              isSystemAdmin
                ? ''
                : t('notifications.admin.system_only', { defaultValue: 'System-wide requires SYSTEM_ADMIN' })
            }
          >
            <option value="tenant">{t('notifications.admin.tenant', { defaultValue: 'This tenant' })}</option>
            {isSystemAdmin && (
              <option value="system">{t('notifications.admin.system', { defaultValue: 'All tenants (system)' })}</option>
            )}
          </select>
          <button
            onClick={handleBroadcast}
            disabled={sending || !bcTitle.trim()}
            className="ml-auto flex items-center px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-bold rounded-lg"
          >
            <Send className="w-3.5 h-3.5 mr-1.5" />
            {sending
              ? t('common.sending', { defaultValue: 'Sending…' })
              : t('notifications.admin.send', { defaultValue: 'Send' })}
          </button>
        </div>
        {error && (
          <p className="text-xs text-red-600 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2 flex items-center">
            <ShieldAlert className="w-3.5 h-3.5 mr-1.5" />
            {error}
          </p>
        )}
      </div>

      {/* Tenant/system feed */}
      <div className="bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
          <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text">
            {isSystemAdmin
              ? t('notifications.admin.system_feed', { defaultValue: 'System feed (all tenants)' })
              : t('notifications.admin.tenant_feed', { defaultValue: 'Tenant feed' })}
          </h3>
          <button
            onClick={load}
            className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {items.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-gray-400">
            {t('notifications.admin.no_items', { defaultValue: 'No notifications.' })}
          </p>
        ) : (
          <ul className="divide-y divide-gray-50 dark:divide-dark-border max-h-[420px] overflow-y-auto">
            {items.map((n) => {
              return (
                <li key={n.id}>
                  <button
                    type="button"
                    onClick={() => openDetail(n.id)}
                    title={t('notifications.admin.view_delivery', {
                      defaultValue: 'View delivery details',
                    })}
                    className="w-full text-left px-4 py-3 hover:bg-blue-50/40 dark:hover:bg-blue-900/10 transition-colors flex items-start justify-between gap-3 group"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-gray-900 dark:text-dark-text truncate group-hover:text-blue-600 dark:group-hover:text-blue-400">
                        {n.title}
                      </p>
                      {n.body && (
                        <p className="text-xs text-gray-500 dark:text-dark-muted line-clamp-1">{n.body}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <span
                        className={`px-1.5 py-0.5 text-[10px] font-bold rounded uppercase ${SOURCE_COLORS[n.source] ?? 'bg-gray-100 text-gray-600'}`}
                      >
                        {n.source}
                      </span>
                      {n.created_at && (
                        <span className="text-[10px] text-gray-400 whitespace-nowrap">
                          {formatDistanceToNow(new Date(n.created_at), {
                            addSuffix: true,
                            locale: dateLocale,
                          })}
                        </span>
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {(detail || detailLoading || detailError) && (
        <DeliveryDetailModal
          detail={detail}
          loading={detailLoading}
          error={detailError}
          dateLocale={dateLocale}
          onClose={closeDetail}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delivery detail modal
// ---------------------------------------------------------------------------

const CHANNEL_LABEL: Record<DeliveryChannel, string> = {
  IN_APP: 'In-app',
  PUSH: 'Push',
  EMAIL: 'Email',
  SMS: 'SMS',
};

function StatusPill({ status }: { status: DeliveryStatus }) {
  const map: Record<DeliveryStatus, { cls: string; icon: React.ReactNode; label: string }> = {
    DELIVERED: {
      cls: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400',
      icon: <CheckCircle2 className="w-3 h-3" />,
      label: 'Delivered',
    },
    SENT: {
      cls: 'bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400',
      icon: <CheckCircle2 className="w-3 h-3" />,
      label: 'Sent',
    },
    PENDING: {
      cls: 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400',
      icon: <Clock className="w-3 h-3" />,
      label: 'Pending',
    },
    FAILED: {
      cls: 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400',
      icon: <XCircle className="w-3 h-3" />,
      label: 'Failed',
    },
  };
  const cfg = map[status];
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-bold rounded uppercase ${cfg.cls}`}>
      {cfg.icon}
      {cfg.label}
    </span>
  );
}

function DeliveryDetailModal({
  detail,
  loading,
  error,
  dateLocale,
  onClose,
}: {
  detail: DeliveryDetail | null;
  loading: boolean;
  error: string | null;
  dateLocale: Locale;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-dark-surface rounded-2xl shadow-xl border border-gray-200 dark:border-dark-border w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between p-5 border-b border-gray-100 dark:border-dark-border">
          <div className="min-w-0 flex-1">
            <h3 className="text-base font-bold text-gray-900 dark:text-dark-text flex items-center gap-2">
              <InboxIcon className="w-4 h-4 text-blue-600 dark:text-blue-400" />
              {loading
                ? 'Loading…'
                : detail
                  ? detail.notification.title
                  : 'Delivery details'}
            </h3>
            {detail?.notification.created_at && (
              <p className="text-xs text-gray-400 mt-1">
                {format(new Date(detail.notification.created_at), 'PPpp', { locale: dateLocale })}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-dark-border rounded-lg"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {error && (
            <p className="text-sm text-red-600 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
              {error}
            </p>
          )}

          {detail && (
            <>
              {/* Meta */}
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <p className="text-gray-400 uppercase font-bold tracking-wider text-[10px]">Source</p>
                  <p className="text-gray-900 dark:text-dark-text font-semibold">
                    {detail.notification.source} · {detail.notification.category}
                  </p>
                </div>
                <div>
                  <p className="text-gray-400 uppercase font-bold tracking-wider text-[10px]">Severity</p>
                  <p className="text-gray-900 dark:text-dark-text font-semibold capitalize">
                    {detail.notification.severity}
                  </p>
                </div>
                <div>
                  <p className="text-gray-400 uppercase font-bold tracking-wider text-[10px]">Sent by</p>
                  <p className="text-gray-900 dark:text-dark-text font-semibold">
                    {detail.sender?.email ?? '— system —'}
                  </p>
                </div>
                <div>
                  <p className="text-gray-400 uppercase font-bold tracking-wider text-[10px]">Recipients</p>
                  <p className="text-gray-900 dark:text-dark-text font-semibold">{detail.recipient_count}</p>
                </div>
              </div>

              {detail.notification.body && (
                <div>
                  <p className="text-gray-400 uppercase font-bold tracking-wider text-[10px] mb-1">Message</p>
                  <p className="text-sm text-gray-700 dark:text-dark-muted whitespace-pre-wrap">
                    {detail.notification.body}
                  </p>
                </div>
              )}

              {/* Recipient breakdown */}
              <div>
                <p className="text-gray-400 uppercase font-bold tracking-wider text-[10px] mb-2">
                  Delivery per recipient
                </p>
                <div className="space-y-2">
                  {detail.recipients.map((r) => (
                    <div
                      key={r.user_id}
                      className="border border-gray-100 dark:border-dark-border rounded-xl p-3 bg-gray-50/50 dark:bg-dark-bg/40"
                    >
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <p className="text-sm font-semibold text-gray-900 dark:text-dark-text truncate">
                          {r.user_email ?? r.user_id}
                        </p>
                        <span className="text-[10px] text-gray-400 uppercase">
                          inbox: {r.inbox_status}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {r.deliveries.length === 0 && (
                          <span className="text-[11px] text-gray-400 italic">No delivery attempts</span>
                        )}
                        {r.deliveries.map((d, i) => (
                          <div
                            key={i}
                            className="flex items-center gap-2 px-2 py-1 bg-white dark:bg-dark-surface rounded-lg border border-gray-100 dark:border-dark-border"
                          >
                            <span className="text-[10px] font-bold uppercase text-gray-500 dark:text-dark-muted min-w-[40px]">
                              {CHANNEL_LABEL[d.channel] ?? d.channel}
                            </span>
                            <StatusPill status={d.status} />
                            {d.error && (
                              <span className="text-[10px] text-red-500 truncate max-w-[200px]" title={d.error}>
                                {d.error}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  loading,
}: {
  icon: React.ReactNode;
  label: string;
  value?: number;
  loading: boolean;
}) {
  return (
    <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl p-3">
      <div className="flex items-center text-gray-400 mb-1">
        {icon}
        <span className="text-[10px] font-bold uppercase tracking-wider ml-1.5">{label}</span>
      </div>
      <p className="text-xl font-bold text-gray-900 dark:text-dark-text">
        {loading ? '—' : value ?? 0}
      </p>
    </div>
  );
}

function sumDelivered(stats: Stats | null): number {
  if (!stats) return 0;
  let total = 0;
  for (const channels of Object.values(stats.delivery)) {
    for (const [status, count] of Object.entries(channels)) {
      if (status === 'DELIVERED' || status === 'SENT') total += count;
    }
  }
  return total;
}

// type-only re-export kept for callers that import rules alongside
export type { NotificationRule };
