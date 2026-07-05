import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Bell,
  Clock,
  CheckCheck,
  CheckSquare,
  Trash2,
  Play,
  RefreshCw,
  Info,
  Smartphone,
  ShieldAlert,
  Inbox as InboxIcon,
} from 'lucide-react';
import { format } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import {
  notificationService,
  type NotificationInboxItem,
  type NotificationTrigger,
  type RecipientStatus,
  type NotificationCategory,
} from '../../services/notificationService';
import { useNotificationStore } from '../../store/notificationStore';
import { NotificationRules } from '../../components/notifications/NotificationRules';
import { AdminNotificationCenter } from '../../components/notifications/AdminNotificationCenter';
import { NotificationItem } from '../../components/notifications/NotificationItem';
import { NotificationDetailModal } from '../../components/notifications/NotificationDetailModal';
import { BulkActionBar } from '../../components/notifications/BulkActionBar';
import { useAuthStore } from '../../store/slices/authSlice';

type Tab = 'inbox' | 'all' | 'rules' | 'triggers' | 'admin';

const VALID_TABS: Tab[] = ['inbox', 'all', 'rules', 'triggers', 'admin'];

export default function NotificationManagement() {
  const { t, i18n } = useTranslation();
  const dateLocale = i18n.language === 'el' ? el : enUS;
  const navigate = useNavigate();
  const { tab: tabParam } = useParams<{ tab?: string }>();

  const { connected, markRead, markDismissed, markAllRead } = useNotificationStore();
  const [items, setItems] = useState<NotificationInboxItem[]>([]);
  const [triggers, setTriggers] = useState<NotificationTrigger[]>([]);
  const [loading, setLoading] = useState(true);
  const [pushStatus, setPushStatus] = useState('Checking...');
  const [statusFilter, setStatusFilter] = useState<RecipientStatus | ''>('');
  const [categoryFilter, setCategoryFilter] = useState<NotificationCategory | ''>('');
  const [sourceFilter, setSourceFilter] = useState<string>('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectMode, setSelectMode] = useState(false);
  const [detail, setDetail] = useState<NotificationInboxItem | null>(null);

  // Validate + normalize the tab param
  const activeTab: Tab = useMemo(() => {
    if (tabParam && (VALID_TABS as string[]).includes(tabParam)) return tabParam as Tab;
    return 'inbox';
  }, [tabParam]);

  const setTab = (next: Tab) => navigate(`/notifications/${next}`, { replace: true });

  // Reset selection + filters when switching tabs
  useEffect(() => {
    setSelected(new Set());
    setSelectMode(false);
  }, [activeTab]);

  const toggleSelectMode = () => {
    setSelectMode((prev) => {
      const next = !prev;
      if (!next) setSelected(new Set());
      return next;
    });
  };

  const loadInbox = useCallback(async () => {
    setLoading(true);
    try {
      const status: RecipientStatus | undefined = activeTab === 'inbox' ? 'unread' : statusFilter || undefined;
      const { items: fetched } = await notificationService.getInbox({
        status,
        category: categoryFilter || undefined,
        source: (sourceFilter as any) || undefined,
        limit: 100,
      });
      setItems(fetched);
    } catch (error) {
      console.error('Failed to load inbox', error);
    } finally {
      setLoading(false);
    }
  }, [activeTab, statusFilter, categoryFilter, sourceFilter]);

  const loadTriggers = useCallback(async () => {
    try {
      const fetched = await notificationService.getTriggers().catch(() => []);
      setTriggers(Array.isArray(fetched) ? fetched : []);
    } catch {
      setTriggers([]);
    }
  }, []);

  useEffect(() => {
    checkPushSubscription();
  }, []);

  useEffect(() => {
    if (activeTab === 'triggers') {
      loadTriggers();
    } else if (activeTab !== 'rules' && activeTab !== 'admin') {
      loadInbox();
    }
  }, [activeTab, loadInbox, loadTriggers]);

  const checkPushSubscription = async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      setPushStatus('Not Supported');
      return;
    }
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      if (!registration) {
        setPushStatus('Not Registered');
        return;
      }
      const subscription = await registration.pushManager.getSubscription();
      setPushStatus(subscription ? 'Subscribed' : 'Not Subscribed');
    } catch {
      setPushStatus('Error');
    }
  };

  // Selection handlers
  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const clearSelection = () => setSelected(new Set());
  const selectAll = () => setSelected(new Set(items.map((i) => i.recipient_id)));

  // Opens the detail modal AND auto-marks unread items as read. Standard
  // inbox UX (Gmail / GitHub / Slack) — opening = reading. In select mode,
  // click toggles selection instead (the detail modal stays closed).
  const openDetail = (item: NotificationInboxItem) => {
    if (selectMode) {
      toggleSelect(item.recipient_id);
      return;
    }
    if (item.status === 'unread') {
      // Optimistic: show the modal in its read state immediately, fire the
      // API + items-list update in the background. Avoids a flash of the
      // "Mark read" button that would otherwise be visible for one frame.
      setDetail({
        ...item,
        status: 'read',
        read_at: new Date().toISOString(),
      });
      handleMarkRead(item.recipient_id);
    } else {
      setDetail(item);
    }
  };

  const handleMarkRead = async (recipientId: string) => {
    await markRead(recipientId);
    setItems((prev) =>
      prev.map((i) =>
        i.recipient_id === recipientId ? { ...i, status: 'read', read_at: new Date().toISOString() } : i
      )
    );
  };
  const handleDismiss = async (recipientId: string) => {
    await markDismissed(recipientId);
    setItems((prev) => prev.filter((i) => i.recipient_id !== recipientId));
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(recipientId);
      return next;
    });
  };
  const handleMarkAllRead = async () => {
    await markAllRead();
    setItems((prev) =>
      prev.map((i) => (i.status === 'unread' ? { ...i, status: 'read', read_at: new Date().toISOString() } : i))
    );
  };
  const handleBulkRead = async () => {
    const ids = Array.from(selected);
    await Promise.all(ids.map((id) => markRead(id)));
    setItems((prev) =>
      prev.map((i) => (selected.has(i.recipient_id) ? { ...i, status: 'read', read_at: new Date().toISOString() } : i))
    );
    clearSelection();
  };
  const handleBulkDismiss = async () => {
    const ids = Array.from(selected);
    await Promise.all(ids.map((id) => markDismissed(id)));
    setItems((prev) => prev.filter((i) => !selected.has(i.recipient_id)));
    clearSelection();
  };

  const handleFixPush = async () => {
    try {
      const { nativeNotificationService } = await import('../../services/nativeNotificationService');
      if (window.Notification && window.Notification.permission === 'denied') {
        alert(
          'Browser notification permission is DENIED. Click the lock icon and set Notifications to "Allow" or "Ask", then try again.'
        );
        return;
      }
      const sub = await nativeNotificationService.subscribeToPush();
      if (sub) setPushStatus('Subscribed');
      else
        alert(
          'Could not enable push. The browser prompt may have been dismissed, the server VAPID keys may be missing, or you are in a Private/Incognito window.'
        );
    } catch (error: any) {
      alert(`Failed to initialize push: ${error.message || 'Unknown error'}`);
    }
  };

  const handleTestTrigger = async (id: string) => {
    try {
      await notificationService.testTrigger(id);
    } catch {
      alert('Failed to test trigger');
    }
  };
  const handleDeleteTrigger = async (id: string) => {
    if (!confirm('Delete this reminder trigger?')) return;
    try {
      await notificationService.deleteTrigger(id);
      setTriggers((prev) => prev.filter((t) => t.id !== id));
    } catch {
      alert('Failed to delete trigger');
    }
  };

  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'ADMIN' || user?.role === 'MANAGER' || user?.role === 'SYSTEM_ADMIN';

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'inbox', label: t('notifications.inbox', { defaultValue: 'Inbox' }), count: items.filter((i) => i.status === 'unread').length },
    { id: 'all', label: t('notifications.all', { defaultValue: 'All' }) },
    { id: 'rules', label: t('notifications.rules_tab', { defaultValue: 'Biomarker Rules' }) },
    { id: 'triggers', label: t('notifications.triggers', { defaultValue: 'Reminders' }) },
    ...(isAdmin ? [{ id: 'admin' as Tab, label: t('notifications.admin_tab', { defaultValue: 'Admin' }) }] : []),
  ];

  const showInboxUI = activeTab === 'inbox' || activeTab === 'all';

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('notifications.center', { defaultValue: 'Notification Center' })}
        subtitle={
          <div className="flex items-center mt-1 space-x-2 flex-wrap">
            <p className="text-gray-500 dark:text-dark-muted">
              {t('notifications.subtitle', { defaultValue: 'Your notifications and reminders' })}
            </p>
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400">
              <span className={`w-1 h-1 rounded-full mr-1 ${connected ? 'bg-green-500 animate-pulse' : 'bg-amber-500'}`} />
              {connected ? 'Live' : 'Reconnecting'}
            </span>
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium ${
                pushStatus === 'Subscribed'
                  ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400'
                  : 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400'
              }`}
            >
              <Smartphone className="w-3 h-3 mr-1" />
              Push: {pushStatus}
            </span>
          </div>
        }
        icon={<Bell className="w-8 h-8" />}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <>
            <button
              onClick={handleFixPush}
              className={`flex items-center px-3 py-2 border rounded-lg text-xs font-bold transition-colors ${
                pushStatus === 'Subscribed'
                  ? 'bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100 dark:bg-indigo-900/20 dark:text-indigo-400 dark:border-indigo-900/40'
                  : 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100 dark:bg-amber-900/20 dark:text-amber-400 dark:border-amber-900/40'
              }`}
            >
              <ShieldAlert className="w-3.5 h-3.5 mr-1.5" />
              {pushStatus === 'Subscribed' ? 'Push Active' : 'Enable Push'}
            </button>
            <button
              onClick={() => {
                setLoading(true);
                activeTab === 'triggers' ? loadTriggers() : loadInbox();
              }}
              className="p-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-gray-500 hover:text-blue-600 transition-colors shadow-sm"
            >
              <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1 p-1 bg-gray-100 dark:bg-dark-border rounded-xl w-fit">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setTab(tab.id)}
              className={`px-4 py-2 text-sm font-bold rounded-lg transition-all flex items-center gap-2 ${
                activeTab === tab.id
                  ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700 dark:hover:text-dark-text'
              }`}
            >
              {tab.label}
              {tab.count !== undefined && tab.count > 0 && (
                <span className="inline-flex items-center justify-center bg-red-500 text-white rounded-full text-[10px] w-4 h-4">
                  {tab.count > 9 ? '9+' : tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {showInboxUI && (
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
              className="text-xs border border-gray-200 dark:border-dark-border rounded-lg px-2 py-1.5 bg-white dark:bg-dark-surface text-gray-700 dark:text-dark-text"
            >
              <option value="">All sources</option>
              <option value="SYSTEM">System</option>
              <option value="SCHEDULED">Scheduled</option>
              <option value="RULE">Biomarker rule</option>
              <option value="AGENT">AI agent</option>
              <option value="INTEGRATION">Integration</option>
              <option value="CLINICAL">Clinical</option>
            </select>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value as NotificationCategory | '')}
              className="text-xs border border-gray-200 dark:border-dark-border rounded-lg px-2 py-1.5 bg-white dark:bg-dark-surface text-gray-700 dark:text-dark-text"
            >
              <option value="">All categories</option>
              <option value="reminder">Reminders</option>
              <option value="alert">Alerts</option>
              <option value="hitl">AI tasks</option>
              <option value="agent">Agent</option>
              <option value="integration">Integrations</option>
              <option value="system">System</option>
              <option value="clinical_event">Clinical events</option>
            </select>
            {activeTab === 'all' && (
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as RecipientStatus | '')}
                className="text-xs border border-gray-200 dark:border-dark-border rounded-lg px-2 py-1.5 bg-white dark:bg-dark-surface text-gray-700 dark:text-dark-text"
              >
                <option value="">Any status</option>
                <option value="unread">Unread</option>
                <option value="read">Read</option>
                <option value="dismissed">Dismissed</option>
              </select>
            )}
            {items.length > 0 && (
              <>
                <button
                  onClick={toggleSelectMode}
                  className={`flex items-center text-xs font-bold px-2 py-1.5 rounded-lg transition-colors ${
                    selectMode
                      ? 'bg-blue-600 text-white hover:bg-blue-700'
                      : 'text-gray-500 hover:text-blue-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-border'
                  }`}
                  title={selectMode ? t('notifications.exit_select', { defaultValue: 'Exit select mode' }) : t('notifications.enter_select', { defaultValue: 'Select multiple' })}
                >
                  <CheckSquare className="w-3.5 h-3.5 mr-1" />
                  {selectMode
                    ? t('notifications.selecting', { defaultValue: 'Selecting' })
                    : t('notifications.select', { defaultValue: 'Select' })}
                </button>
                {selectMode && (
                  <button
                    onClick={selectAll}
                    className="text-xs font-bold text-gray-500 hover:text-blue-600 dark:text-dark-muted px-2 py-1.5"
                  >
                    {t('notifications.select_all', { defaultValue: 'Select all' })}
                  </button>
                )}
                {!selectMode && items.some((i) => i.status === 'unread') && (
                  <button
                    onClick={handleMarkAllRead}
                    className="flex items-center text-xs font-bold text-blue-600 hover:text-blue-700 dark:text-blue-400 px-2 py-1.5"
                  >
                    <CheckCheck className="w-3.5 h-3.5 mr-1" />
                    {t('common.mark_all_read', { defaultValue: 'Mark all read' })}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {activeTab === 'triggers' ? (
        <TriggersTab
          triggers={triggers}
          loading={loading}
          onTest={handleTestTrigger}
          onDelete={handleDeleteTrigger}
          dateLocale={dateLocale}
        />
      ) : activeTab === 'rules' ? (
        <NotificationRules />
      ) : activeTab === 'admin' ? (
        <AdminNotificationCenter />
      ) : (
        <>
          <BulkActionBar
            selectedCount={selected.size}
            onClearSelection={() => {
              clearSelection();
              setSelectMode(false);
            }}
            onMarkSelectedRead={handleBulkRead}
            onDismissSelected={handleBulkDismiss}
          />
          <InboxList
            items={items}
            loading={loading}
            dateLocale={dateLocale}
            selectMode={selectMode}
            selected={selected}
            onToggleSelect={toggleSelect}
            onClick={openDetail}
            onMarkRead={handleMarkRead}
            onDismiss={handleDismiss}
          />
        </>
      )}

      {detail && (
        <NotificationDetailModal
          item={detail}
          dateLocale={dateLocale}
          onClose={() => setDetail(null)}
          onMarkRead={handleMarkRead}
        />
      )}

      <div className="bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-2xl p-4 flex items-start space-x-3">
        <Info className="w-5 h-5 text-blue-500 flex-shrink-0 mt-0.5" />
        <div className="text-sm text-blue-800 dark:text-blue-300 leading-relaxed">
          <p className="font-bold mb-1">{t('notifications.debug_title', { defaultValue: 'Real-time delivery' })}</p>
          <p>
            {t('notifications.debug_body', {
              defaultValue:
                'Notifications stream over a live per-user WebSocket. Click any item to see details and any action buttons. Push delivery uses Web Push (VAPID) — enable it above.',
            })}
          </p>
        </div>
      </div>
    </div>
  );
}

function InboxList({
  items,
  loading,
  dateLocale,
  selectMode,
  selected,
  onToggleSelect,
  onClick,
  onMarkRead,
  onDismiss,
}: {
  items: NotificationInboxItem[];
  loading: boolean;
  dateLocale: Locale;
  selectMode: boolean;
  selected: Set<string>;
  onToggleSelect: (id: string) => void;
  onClick: (item: NotificationInboxItem) => void;
  onMarkRead: (recipientId: string) => Promise<void>;
  onDismiss: (recipientId: string) => Promise<void>;
}) {
  if (loading && items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border">
        <RefreshCw className="w-8 h-8 text-blue-600 animate-spin mb-4" />
        <p className="text-gray-500">Loading…</p>
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="py-20 text-center bg-white dark:bg-dark-surface rounded-2xl border-2 border-dashed border-gray-100 dark:border-dark-border">
        <InboxIcon className="w-12 h-12 text-gray-200 mx-auto mb-4" />
        <p className="text-gray-400">Nothing here yet.</p>
      </div>
    );
  }
  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
      <ul className="divide-y divide-gray-50 dark:divide-dark-border">
        {items.map((item) => (
          <NotificationItem
            key={item.recipient_id}
            item={item}
            dateLocale={dateLocale}
            selectMode={selectMode}
            selected={selected.has(item.recipient_id)}
            onToggleSelect={onToggleSelect}
            onClick={onClick}
            onMarkRead={onMarkRead}
            onDismiss={onDismiss}
          />
        ))}
      </ul>
    </div>
  );
}

function TriggersTab({
  triggers,
  loading,
  onTest,
  onDelete,
  dateLocale,
}: {
  triggers: NotificationTrigger[];
  loading: boolean;
  onTest: (id: string) => void;
  onDelete: (id: string) => void;
  dateLocale: Locale;
}) {
  if (loading && triggers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border">
        <RefreshCw className="w-8 h-8 text-blue-600 animate-spin mb-4" />
        <p className="text-gray-500">Loading…</p>
      </div>
    );
  }
  if (triggers.length === 0) {
    return (
      <div className="py-20 text-center bg-white dark:bg-dark-surface rounded-2xl border-2 border-dashed border-gray-100 dark:border-dark-border">
        <Clock className="w-12 h-12 text-gray-200 mx-auto mb-4" />
        <p className="text-gray-400">No active scheduled reminders.</p>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {triggers.map((trig) => (
        <div
          key={trig.id}
          className="bg-white dark:bg-dark-surface rounded-2xl p-5 border border-gray-100 dark:border-dark-border shadow-sm hover:shadow-md transition-all"
        >
          <div className="flex items-start justify-between mb-4">
            <div
              className={`p-2 rounded-xl ${
                trig.enabled
                  ? 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/20 dark:text-indigo-400'
                  : 'bg-gray-50 text-gray-400 dark:bg-dark-bg'
              }`}
            >
              <Clock className="w-5 h-5" />
            </div>
            <div className="flex items-center space-x-1">
              <button
                onClick={() => onTest(trig.id)}
                title="Test now"
                className="p-2 text-gray-400 hover:text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20 rounded-lg transition-colors"
              >
                <Play className="w-4 h-4" />
              </button>
              <button
                onClick={() => onDelete(trig.id)}
                className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
          <h3 className="font-bold text-gray-900 dark:text-dark-text mb-1">{trig.title}</h3>
          <p className="text-xs text-gray-500 dark:text-dark-muted mb-4 line-clamp-2">{trig.body}</p>
          <div className="space-y-2 pt-4 border-t border-gray-50 dark:border-dark-border">
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-gray-400 uppercase font-bold tracking-wider">Next run</span>
              <span className="text-blue-600 dark:text-blue-400 font-bold">
                {trig.next_trigger
                  ? format(new Date(trig.next_trigger), 'MMM d, HH:mm', { locale: dateLocale })
                  : 'Disabled'}
              </span>
            </div>
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-gray-400 uppercase font-bold tracking-wider">Status</span>
              <div className="flex items-center">
                <span className={`flex h-2 w-2 rounded-full mr-1.5 ${trig.enabled ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
                <span className={trig.enabled ? 'text-green-600 dark:text-green-400 font-bold' : 'text-gray-400'}>
                  {trig.enabled ? 'Active' : 'Disabled'}
                </span>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// type-only re-export kept for the isolated Locale usage above
import type { Locale } from 'date-fns';
