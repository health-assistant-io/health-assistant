import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Bell,
  ShieldAlert,
  RefreshCw,
  Smartphone,
  Wifi,
  WifiOff,
  CheckCircle2,
  XCircle,
  SlidersHorizontal,
  ExternalLink,
} from 'lucide-react';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useNotificationStore } from '../../store/notificationStore';
import { useNotificationPreferences } from '../../hooks/useNotificationPreferences';
import type { NotificationKindState } from '../../services/notificationService';

/**
 * The canonical notification-preferences surface.
 *
 * Replaces the old ``pages/Settings/Notifications.tsx`` (deleted). Renders
 * under the Notification Center's Settings tab at ``/notifications/settings``
 * and is the single source of truth for:
 *
 * - Browser permission / push subscription / real-time status
 * - The master enable toggle + push setup actions
 * - Every addressable notification kind (sources + channels + per-integration-
 *   instance types), read from the unified ``GET /notifications/preferences``
 *   endpoint and mutated via ``PUT /notifications/preferences/{kind_id}``.
 */
type PushStatus =
  | 'Subscribed'
  | 'Not Subscribed'
  | 'Not Registered'
  | 'Not Supported'
  | 'Checking...'
  | 'Error';

export function NotificationSettings() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { notificationsEnabled, setNotificationsEnabled } = useSettingsStore();
  const connected = useNotificationStore((s) => s.connected);
  const { preferences, loading: prefsLoading, setKind } = useNotificationPreferences({
    autoFetch: true,
  });

  const [permission, setPermission] = useState<NotificationPermission>(
    typeof Notification !== 'undefined' ? Notification.permission : 'denied'
  );
  const [pushStatus, setPushStatus] = useState<PushStatus>('Checking...');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [savingKind, setSavingKind] = useState<string | null>(null);

  const refreshPushStatus = useCallback(async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      setPushStatus('Not Supported');
      return;
    }
    setPushStatus('Checking...');
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
  }, []);

  useEffect(() => {
    refreshPushStatus();
  }, [refreshPushStatus]);

  const handleRequestPermission = useCallback(async () => {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      const { nativeNotificationService } = await import('../../services/nativeNotificationService');
      const result = await nativeNotificationService.requestPermission();
      setPermission(result);
      if (result !== 'granted') {
        setError(
          t('settings.notifications_blocked', {
            defaultValue:
              'Browser permission is BLOCKED. Click the padlock icon in the address bar and set Notifications to "Allow", then retry.',
          })
        );
      } else {
        setInfo(t('settings.notifications_granted', { defaultValue: 'Permission granted.' }));
      }
    } finally {
      setBusy(false);
    }
  }, [t]);

  const handleSubscribe = useCallback(async () => {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      if (permission !== 'granted') {
        const { nativeNotificationService } = await import('../../services/nativeNotificationService');
        const granted = await nativeNotificationService.requestPermission();
        setPermission(granted);
        if (granted !== 'granted') {
          setError(
            t('settings.notifications_blocked', {
              defaultValue:
                'Browser permission is BLOCKED. Click the padlock icon in the address bar and set Notifications to "Allow", then retry.',
            })
          );
          return;
        }
      }
      const { nativeNotificationService } = await import('../../services/nativeNotificationService');
      const sub = await nativeNotificationService.subscribeToPush();
      if (sub) {
        setNotificationsEnabled(true);
        setPushStatus('Subscribed');
        setInfo(
          t('settings.push_subscribed', {
            defaultValue: 'Successfully subscribed to push notifications!',
          })
        );
      } else {
        setError(
          t('settings.push_failed', {
            defaultValue:
              'Could not subscribe. The browser prompt may have been dismissed, the server VAPID keys may be missing, or you are in a Private/Incognito window.',
          })
        );
      }
    } catch (err: any) {
      setError(err?.message ?? 'Subscription failed');
    } finally {
      setBusy(false);
    }
  }, [permission, setNotificationsEnabled, t]);

  const handleUnsubscribe = useCallback(async () => {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      const reg = await navigator.serviceWorker?.getRegistration?.();
      const sub = await reg?.pushManager?.getSubscription();
      if (sub) {
        await sub.unsubscribe();
      }
      setPushStatus('Not Subscribed');
      setNotificationsEnabled(false);
      setInfo(
        t('settings.push_unsubscribed', {
          defaultValue:
            'Unsubscribed. The backend subscription will be pruned on the next delivery attempt.',
        })
      );
    } catch (err: any) {
      setError(err?.message ?? 'Unsubscribe failed');
    } finally {
      setBusy(false);
    }
  }, [setNotificationsEnabled, t]);

  const handleTestPush = useCallback(async () => {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      if (Notification.permission === 'granted') {
        new Notification(t('settings.test_local_title', { defaultValue: 'Local test' }), {
          body: t('settings.test_local_body', {
            defaultValue: 'If you see this, the SW + permission are working.',
          }),
          icon: '/icon.svg',
        });
        setInfo(
          t('settings.test_local_info', {
            defaultValue:
              'Local notification shown. To test the full push path, ask an admin to send a broadcast.',
          })
        );
      } else {
        setError(t('settings.notifications_blocked', { defaultValue: 'Permission not granted.' }));
      }
    } finally {
      setBusy(false);
    }
  }, [t]);

  // Unified kind-toggle handler (optimistic via the hook; surfaces errors).
  const toggleKind = useCallback(
    async (kind: NotificationKindState) => {
      setSavingKind(kind.kind_id);
      try {
        await setKind(kind.kind_id, !kind.enabled);
      } catch (err: any) {
        setError(err?.response?.data?.detail ?? 'Could not update preference');
      } finally {
        setSavingKind(null);
      }
    },
    [setKind]
  );

  const sources = useMemo(
    () => (preferences ?? []).filter((p) => p.group === 'source'),
    [preferences]
  );
  const channels = useMemo(
    () => (preferences ?? []).filter((p) => p.group === 'channel'),
    [preferences]
  );
  const integrationGroups = useMemo(
    () => groupIntegrationKindsByInstance((preferences ?? []).filter((p) => p.group === 'integration')),
    [preferences]
  );

  const isPushReady =
    pushStatus === 'Subscribed' && permission === 'granted' && notificationsEnabled;

  return (
    <div className="space-y-6">
      {/* Status cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <StatusCard
          icon={permission === 'granted' ? <CheckCircle2 className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
          label={t('settings.notifications_permission', { defaultValue: 'Browser Permission' })}
          value={permission}
          tone={permission === 'granted' ? 'ok' : 'bad'}
        />
        <StatusCard
          icon={<Smartphone className="w-4 h-4" />}
          label={t('settings.notifications_push', { defaultValue: 'Push Subscription' })}
          value={pushStatus}
          tone={pushStatus === 'Subscribed' ? 'ok' : pushStatus === 'Not Supported' ? 'bad' : 'warn'}
        />
        <StatusCard
          icon={connected ? <Wifi className="w-4 h-4" /> : <WifiOff className="w-4 h-4" />}
          label={t('settings.notifications_realtime', { defaultValue: 'Real-time Stream' })}
          value={
            connected
              ? t('common.live', { defaultValue: 'Live' })
              : t('common.reconnecting', { defaultValue: 'Reconnecting' })
          }
          tone={connected ? 'ok' : 'warn'}
        />
      </div>

      {/* Master toggle + push setup */}
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-dark-text">
              {t('settings.notifications_enabled', { defaultValue: 'Notifications' })}
            </p>
            <p className="text-sm text-gray-500 dark:text-dark-muted">
              {notificationsEnabled
                ? t('admin.active', { defaultValue: 'Enabled' })
                : t('common.inactive', { defaultValue: 'Disabled' })}
            </p>
          </div>
          <button
            onClick={() => setNotificationsEnabled(!notificationsEnabled)}
            disabled={busy}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
              notificationsEnabled
                ? 'bg-green-600 text-white hover:bg-green-700'
                : 'bg-gray-200 dark:bg-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-300'
            }`}
          >
            {notificationsEnabled
              ? t('admin.active', { defaultValue: 'Enabled' })
              : t('common.inactive', { defaultValue: 'Disabled' })}
          </button>
        </div>

        <div className="border-t border-gray-100 dark:border-dark-border pt-5 space-y-3">
          <button
            onClick={handleRequestPermission}
            disabled={busy || permission === 'granted'}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 border border-gray-200 dark:border-dark-border rounded-lg text-sm font-medium text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg disabled:opacity-50"
          >
            <ShieldAlert className="w-4 h-4" />
            {permission === 'granted'
              ? t('settings.permission_already_granted', { defaultValue: 'Permission already granted' })
              : t('settings.request_permission', { defaultValue: 'Request browser permission' })}
          </button>

          {pushStatus !== 'Subscribed' ? (
            <button
              onClick={handleSubscribe}
              disabled={busy}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-semibold disabled:opacity-50"
            >
              <Bell className="w-4 h-4" />
              {t('settings.enable_push', { defaultValue: 'Enable push notifications' })}
            </button>
          ) : (
            <div className="flex gap-2">
              <button
                onClick={handleTestPush}
                disabled={busy}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 border border-blue-200 dark:border-blue-900/40 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 rounded-lg text-sm font-semibold hover:bg-blue-100"
              >
                <RefreshCw className="w-4 h-4" />
                {t('settings.test_push', { defaultValue: 'Send test notification' })}
              </button>
              <button
                onClick={handleUnsubscribe}
                disabled={busy}
                className="flex items-center justify-center gap-2 px-4 py-2 border border-red-200 dark:border-red-900/40 text-red-700 dark:text-red-400 rounded-lg text-sm font-semibold hover:bg-red-50 dark:hover:bg-red-900/20"
              >
                {t('common.unsubscribe', { defaultValue: 'Unsubscribe' })}
              </button>
            </div>
          )}
        </div>

        {error && (
          <p className="text-xs text-red-600 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2 flex items-start gap-2">
            <ShieldAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>{error}</span>
          </p>
        )}
        {info && (
          <p className="text-xs text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 rounded-lg px-3 py-2">
            {info}
          </p>
        )}

        {!isPushReady && (
          <p className="text-xs text-gray-400 dark:text-dark-muted leading-relaxed">
            {t('settings.push_setup_hint', {
              defaultValue:
                'Push delivery requires: (1) browser permission granted, (2) a service worker registration, (3) a VAPID subscription registered with the backend. Enable each in turn above.',
            })}
          </p>
        )}
      </div>

      {/* Unified kind preferences (sources + channels + per-integration types) */}
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6">
        <div className="flex items-center gap-2 mb-1">
          <SlidersHorizontal className="w-4 h-4 text-gray-400" />
          <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text">
            {t('settings.notifications_per_source', {
              defaultValue: 'Notification preferences',
            })}
          </h3>
        </div>
        <p className="text-xs text-gray-500 dark:text-dark-muted mb-5">
          {t('settings.notifications_per_source_desc', {
            defaultValue:
              'Choose which notifications you receive. Muting a kind here or directly from a notification has the same effect.',
          })}
        </p>

        <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-2">
          {t('notifications.sources_label', { defaultValue: 'Sources' })}
        </p>
        <div className="space-y-2 mb-6">
          {prefsLoading && sources.length === 0 ? (
            <PrefSkeleton />
          ) : (
            sources.map((k) => (
              <KindToggle
                key={k.kind_id}
                kind={k}
                saving={savingKind === k.kind_id}
                onChange={() => toggleKind(k)}
              />
            ))
          )}
        </div>

        <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-2 mt-6 pt-4 border-t border-gray-100 dark:border-dark-border">
          {t('notifications.channels_label', { defaultValue: 'Channels' })}
        </p>
        <div className="space-y-2">
          {prefsLoading && channels.length === 0 ? (
            <PrefSkeleton />
          ) : (
            channels.map((k) => (
              <KindToggle
                key={k.kind_id}
                kind={k}
                saving={savingKind === k.kind_id}
                onChange={() => toggleKind(k)}
              />
            ))
          )}
        </div>

        {integrationGroups.length > 0 && (
          <div className="border-t border-gray-100 dark:border-dark-border pt-5 mt-6">
            <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-1">
              {t('notifications.per_integration_label', { defaultValue: 'Per integration' })}
            </p>
            <p className="text-xs text-gray-500 dark:text-dark-muted mb-4">
              {t('notifications.per_integration_desc', {
                defaultValue: 'Each integration instance can be muted independently.',
              })}
            </p>
            <div className="space-y-4">
              {integrationGroups.map((g) => (
                <IntegrationInstanceCard key={g.manageUrl} group={g} savingKind={savingKind} onToggle={toggleKind} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Integration instance grouping
// ---------------------------------------------------------------------------

interface IntegrationInstanceGroup {
  manageUrl: string;
  instanceName: string;
  kinds: NotificationKindState[];
}

/** Group integration kinds by their instance (one group per manage_url). */
function groupIntegrationKindsByInstance(
  kinds: NotificationKindState[]
): IntegrationInstanceGroup[] {
  const byUrl = new Map<string, IntegrationInstanceGroup>();
  for (const k of kinds) {
    // The label is "{typeLabel} — {instanceName}"; extract the instance name.
    const instanceName = k.label.split(' — ').pop() ?? k.label;
    if (!byUrl.has(k.manage_url)) {
      byUrl.set(k.manage_url, { manageUrl: k.manage_url, instanceName, kinds: [] });
    }
    byUrl.get(k.manage_url)!.kinds.push(k);
  }
  return Array.from(byUrl.values());
}

function IntegrationInstanceCard({
  group,
  savingKind,
  onToggle,
}: {
  group: IntegrationInstanceGroup;
  savingKind: string | null;
  onToggle: (k: NotificationKindState) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <div className="border border-gray-100 dark:border-dark-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-bold text-gray-900 dark:text-dark-text">
          {group.instanceName}
        </p>
        <button
          onClick={() => navigate(group.manageUrl)}
          className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline"
        >
          <ExternalLink className="w-3 h-3" />
          {t('common.open', { defaultValue: 'Open' })}
        </button>
      </div>
      <div className="space-y-2">
        {group.kinds.map((k) => (
          <KindToggle
            key={k.kind_id}
            kind={k}
            saving={savingKind === k.kind_id}
            onChange={() => onToggle(k)}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Presentational helpers
// ---------------------------------------------------------------------------

function KindToggle({
  kind,
  saving,
  onChange,
}: {
  kind: NotificationKindState;
  saving: boolean;
  onChange: () => void;
}) {
  const disabled = !kind.mutable;
  return (
    <div className="flex items-start justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-gray-900 dark:text-dark-text">{kind.label}</p>
      </div>
      <button
        onClick={onChange}
        disabled={disabled || saving}
        className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
          kind.enabled ? 'bg-blue-600' : 'bg-gray-300 dark:bg-dark-border'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        title={disabled ? 'Cannot be disabled' : undefined}
      >
        <span
          className={`inline-block h-4 w-4 bg-white rounded-full shadow transform transition-transform mt-0.5 ${
            kind.enabled ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}

function PrefSkeleton() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-8 bg-gray-100 dark:bg-dark-bg rounded-lg animate-pulse" />
      ))}
    </div>
  );
}

function StatusCard({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: 'ok' | 'warn' | 'bad';
}) {
  const toneCls =
    tone === 'ok'
      ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20'
      : tone === 'warn'
        ? 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20'
        : 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20';
  return (
    <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl p-3">
      <div className="flex items-center text-gray-400 mb-1">
        <span className={`p-1 rounded-md mr-1.5 ${toneCls}`}>{icon}</span>
        <span className="text-[10px] font-bold uppercase tracking-wider">{label}</span>
      </div>
      <p className="text-sm font-semibold text-gray-900 dark:text-dark-text capitalize">{value}</p>
    </div>
  );
}
