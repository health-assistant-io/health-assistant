import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Bell, ShieldAlert, RefreshCw, Smartphone, Wifi, WifiOff, CheckCircle2, XCircle, SlidersHorizontal } from 'lucide-react';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useNotificationStore } from '../../store/notificationStore';
import { settingsService } from '../../services/settingsService';
import { integrationService, type IntegrationNotificationGroup } from '../../services/integrationService';
import { PageHeader } from '../../components/ui/PageHeader';

type PushStatus = 'Subscribed' | 'Not Subscribed' | 'Not Registered' | 'Not Supported' | 'Checking...' | 'Error';

const SOURCES = ['SYSTEM', 'SCHEDULED', 'RULE', 'AGENT', 'INTEGRATION', 'CLINICAL'] as const;
const CHANNELS = ['IN_APP', 'PUSH', 'EMAIL'] as const;
type SourceKey = (typeof SOURCES)[number];
type ChannelKey = (typeof CHANNELS)[number];

const SOURCE_DESCRIPTIONS: Record<SourceKey, string> = {
  SYSTEM: 'System-wide notices and admin broadcasts',
  SCHEDULED: 'Medication + examination reminders',
  RULE: 'Biomarker threshold alerts (e.g. out-of-range lab value)',
  AGENT: 'AI agent proposals needing your review (HITL tasks)',
  INTEGRATION: 'Wearable/lab sync outcomes + sync failures',
  CLINICAL: 'Clinical-event lifecycle (care team updates)',
};

const CHANNEL_DESCRIPTIONS: Record<ChannelKey, string> = {
  IN_APP: 'Inbox (always available; no setup needed)',
  PUSH: 'Web Push notifications on this device (requires browser permission)',
  EMAIL: 'Email (requires SMTP setup; off by default)',
};

function Notifications() {
  const { t } = useTranslation();
  const { notificationsEnabled, setNotificationsEnabled } = useSettingsStore();
  const connected = useNotificationStore((s) => s.connected);

  const [permission, setPermission] = useState<NotificationPermission>(
    typeof Notification !== 'undefined' ? Notification.permission : 'denied'
  );
  const [pushStatus, setPushStatus] = useState<PushStatus>('Checking...');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // Per-source/channel preferences (server-side tiered)
  const [sourcePrefs, setSourcePrefs] = useState<Record<SourceKey, boolean>>(
    () => Object.fromEntries(SOURCES.map((s) => [s, true])) as Record<SourceKey, boolean>
  );
  const [channelPrefs, setChannelPrefs] = useState<Record<ChannelKey, boolean>>(
    () => Object.fromEntries(CHANNELS.map((c) => [c, c === 'EMAIL' ? false : true])) as Record<ChannelKey, boolean>
  );
  const [prefsLoading, setPrefsLoading] = useState(true);
  const [prefsSaving, setPrefsSaving] = useState<string | null>(null); // key being saved

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

  // Load per-source/channel preferences from /settings/effective
  useEffect(() => {
    (async () => {
      setPrefsLoading(true);
      try {
        const { settings } = await settingsService.getEffective();
        const nextSources = {} as Record<SourceKey, boolean>;
        for (const s of SOURCES) {
          const key = `notifications.sources.${s}`;
          nextSources[s] = settings[key] !== false; // default true
        }
        const nextChannels = {} as Record<ChannelKey, boolean>;
        for (const c of CHANNELS) {
          const key = `notifications.channels.${c}`;
          // Default: IN_APP + PUSH true, EMAIL false
          const def = c === 'EMAIL' ? false : true;
          if (key in settings) nextChannels[c] = !!settings[key];
          else nextChannels[c] = def;
        }
        setSourcePrefs(nextSources);
        setChannelPrefs(nextChannels);
      } catch (err) {
        // effective settings may not be reachable; fall back to defaults
      } finally {
        setPrefsLoading(false);
      }
    })();
  }, []);

  const savePref = useCallback(async (key: string, value: boolean) => {
    setPrefsSaving(key);
    try {
      await settingsService.updateOverride('user', key, value);
    } catch (err: any) {
      setError(err?.message ?? `Failed to save ${key}`);
      // Best-effort: revert by reloading
      try {
        const { settings } = await settingsService.getEffective();
        if (key.startsWith('notifications.sources.')) {
          const s = key.split('.')[2] as SourceKey;
          setSourcePrefs((p) => ({ ...p, [s]: settings[key] !== false }));
        } else if (key.startsWith('notifications.channels.')) {
          const c = key.split('.')[2] as ChannelKey;
          setChannelPrefs((p) => ({ ...p, [c]: !!settings[key] }));
        }
      } catch {
        // noop
      }
    } finally {
      setPrefsSaving(null);
    }
  }, []);

  const toggleSourcePref = (s: SourceKey) => {
    const next = !sourcePrefs[s];
    setSourcePrefs((p) => ({ ...p, [s]: next }));
    savePref(`notifications.sources.${s}`, next);
  };
  const toggleChannelPref = (c: ChannelKey) => {
    const next = !channelPrefs[c];
    setChannelPrefs((p) => ({ ...p, [c]: next }));
    savePref(`notifications.channels.${c}`, next);
  };

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
          defaultValue: 'Unsubscribed. The backend subscription will be pruned on the next delivery attempt.',
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
      // Trigger a system broadcast to ourselves by re-using the admin broadcast
      // endpoint when available; otherwise just show a local notification.
      if (Notification.permission === 'granted') {
        new Notification(t('settings.test_local_title', { defaultValue: 'Local test' }), {
          body: t('settings.test_local_body', { defaultValue: 'If you see this, the SW + permission are working.' }),
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

  const isPushReady =
    pushStatus === 'Subscribed' && permission === 'granted' && notificationsEnabled;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('settings.nav_notifications', { defaultValue: 'Notifications' })}
        subtitle={t('settings.notifications_subtitle', {
          defaultValue: 'Browser permissions, push delivery, and real-time status',
        })}
        icon={<Bell className="w-8 h-8" />}
      />

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
          value={connected ? t('common.live', { defaultValue: 'Live' }) : t('common.reconnecting', { defaultValue: 'Reconnecting' })}
          tone={connected ? 'ok' : 'warn'}
        />
      </div>

      {/* Master toggle + actions */}
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
            {notificationsEnabled ? t('admin.active', { defaultValue: 'Enabled' }) : t('common.inactive', { defaultValue: 'Disabled' })}
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
                'Push delivery requires: (1) browser permission granted, (2) a service worker registration, (3) a VAPID subscription registered with the backend. Enable each in turn above. If your OS is in Do-Not-Disturb mode, notifications may be suppressed even when fully subscribed.',
            })}
          </p>
        )}
      </div>

      {/* Per-source + per-channel preferences */}
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6">
        <div className="flex items-center gap-2 mb-1">
          <SlidersHorizontal className="w-4 h-4 text-gray-400" />
          <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text">
            {t('settings.notifications_per_source', { defaultValue: 'Per-source & channel preferences' })}
          </h3>
        </div>
        <p className="text-xs text-gray-500 dark:text-dark-muted mb-5">
          {t('settings.notifications_per_source_desc', {
            defaultValue:
              'Choose which notification sources you receive, and on which channels. Saved to your account (USER > TENANT > SYSTEM).',
          })}
        </p>

        <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-2">Sources</p>
        <div className="space-y-2 mb-6">
          {SOURCES.map((s) => (
            <PrefToggle
              key={s}
              label={s}
              description={SOURCE_DESCRIPTIONS[s]}
              checked={sourcePrefs[s]}
              disabled={prefsLoading}
              saving={prefsSaving === `notifications.sources.${s}`}
              onChange={() => toggleSourcePref(s)}
            />
          ))}
        </div>

        <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-2 mt-6 pt-4 border-t border-gray-100 dark:border-dark-border">
          Channels
        </p>
        <div className="space-y-2">
          {CHANNELS.map((c) => (
            <PrefToggle
              key={c}
              label={c === 'IN_APP' ? 'In-app' : c === 'PUSH' ? 'Push' : 'Email'}
              description={CHANNEL_DESCRIPTIONS[c]}
              checked={channelPrefs[c]}
              disabled={prefsLoading || c === 'EMAIL'} // EMAIL disabled until SMTP wired
              saving={prefsSaving === `notifications.channels.${c}`}
              onChange={() => toggleChannelPref(c)}
            />
          ))}
        </div>

        {/* Per-integration notification-type rollup */}
        <IntegrationNotifTypesRollup />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-integration rollup: aggregates every enabled integration's declared
// notification types in one collapsible section. Conditional on the user
// having at least one integration that exposes types.
// ---------------------------------------------------------------------------

function IntegrationNotifTypesRollup() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [groups, setGroups] = useState<IntegrationNotificationGroup[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null); // `${domain}:${typeId}`

  useEffect(() => {
    integrationService
      .getNotificationTypes()
      .then((res) => setGroups(res.integrations ?? []))
      .catch(() => setGroups([]))
      .finally(() => setLoading(false));
  }, []);

  const totalTypes = groups.reduce((sum, g) => sum + g.types.length, 0);

  // Don't render anything until loaded; if no integrations declare types, hide.
  if (loading) return null;
  if (totalTypes === 0) return null;

  const handleToggle = async (domain: string, typeId: string, next: boolean) => {
    setBusy(`${domain}:${typeId}`);
    setGroups((prev) =>
      prev.map((g) =>
        g.domain === domain
          ? { ...g, types: g.types.map((tt) => (tt.id === typeId ? { ...tt, enabled: next } : tt)) }
          : g
      )
    );
    try {
      await integrationService.updateNotificationTypePref(domain, typeId, next);
    } catch (err: any) {
      // Revert
      setGroups((prev) =>
        prev.map((g) =>
          g.domain === domain
            ? { ...g, types: g.types.map((tt) => (tt.id === typeId ? { ...tt, enabled: !next } : tt)) }
            : g
        )
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="border-t border-gray-100 dark:border-dark-border pt-5 mt-6">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between text-left mb-2"
      >
        <div>
          <p className="text-[10px] text-gray-400 uppercase font-bold tracking-wider mb-1">
            Advanced
          </p>
          <p className="text-sm font-bold text-gray-900 dark:text-dark-text">
            {t('settings.notifications_per_integration', {
              defaultValue: 'Per-integration notification types',
            })}
          </p>
          <p className="text-xs text-gray-500 dark:text-dark-muted">
            {totalTypes} type{totalTypes === 1 ? '' : 's'} across {groups.length} integration{groups.length === 1 ? '' : 's'}
          </p>
        </div>
        <span className={`text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
      </button>

      {expanded && (
        <div className="space-y-4 mt-4">
          {groups.map((g) => (
            <div
              key={g.domain}
              className="border border-gray-100 dark:border-dark-border rounded-xl p-4"
            >
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-bold text-gray-900 dark:text-dark-text capitalize">
                  {g.domain.replace(/_/g, ' ')}
                </p>
                <button
                  onClick={() => navigate(`/settings/integrations/${g.integration_id}?tab=notifications`)}
                  className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline"
                >
                  Open integration →
                </button>
              </div>
              <div className="space-y-2">
                {g.types.map((tt) => {
                  const key = `${g.domain}:${tt.id}`;
                  return (
                    <div
                      key={key}
                      className="flex items-start justify-between gap-3 py-1.5"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <p className="text-sm font-semibold text-gray-900 dark:text-dark-text">{tt.label}</p>
                          <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted">
                            {tt.category}
                          </span>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-dark-muted">{tt.description}</p>
                      </div>
                      <button
                        onClick={() => handleToggle(g.domain, tt.id, !tt.enabled)}
                        disabled={busy === key}
                        className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors mt-1 disabled:opacity-50 ${
                          tt.enabled ? 'bg-blue-600' : 'bg-gray-300 dark:bg-dark-border'
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 bg-white rounded-full shadow transform transition-transform mt-0.5 ${
                            tt.enabled ? 'translate-x-4' : 'translate-x-0.5'
                          }`}
                        />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PrefToggle({
  label,
  description,
  checked,
  disabled,
  saving,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  saving: boolean;
  onChange: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-gray-900 dark:text-dark-text">{label}</p>
        <p className="text-xs text-gray-500 dark:text-dark-muted">{description}</p>
      </div>
      <button
        onClick={onChange}
        disabled={disabled || saving}
        className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
          checked ? 'bg-blue-600' : 'bg-gray-300 dark:bg-dark-border'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        title={disabled ? 'Not configurable' : undefined}
      >
        <span
          className={`inline-block h-4 w-4 bg-white rounded-full shadow transform transition-transform mt-0.5 ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
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

export default Notifications;
