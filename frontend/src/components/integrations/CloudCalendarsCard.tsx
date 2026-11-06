import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { CalendarDays, Loader2, Plus, RefreshCw, Trash2, Unplug } from 'lucide-react';

import api from '../../api/axios';
import { usePatientStore } from '../../store/slices/patientSlice';

// Cloud calendars (ADR-0052 / plan 52 P2). Providers, scopes and redaction
// descriptions render from the backend catalog — this card never carries a
// provider list of its own. Credentials go to the single secret endpoint or
// the OAuth round-trip; the response never contains a token.

interface CalendarProvider {
  id: string;
  label: string;
  authKinds: string[];
  baseUrlRequired: boolean;
  protocol: string;
  writable: boolean;
  supportsDelta: boolean;
  scopePresets: Record<string, string[]>;
  consentNotice: string;
  /** Verbatim notice per auth kind (E12: the notice follows the CHOSEN kind,
   *  never the catalog row's first kind — a token ICS bind must never read
   *  "needs no credential"). */
  consentNotices?: Record<string, string>;
  domains: string[];
  /** The bind form renders ONLY from this (ADR-0052 §11 / Rev 6). */
  setupFields?: SetupField[];
}

interface RedactionProfile {
  id: string;
  description: string;
}

interface CalendarCatalog {
  providers: CalendarProvider[];
  redactionProfiles: RedactionProfile[];
  redactionDefault: string;
  directions: string[];
  accountStates: string[];
}

interface CalendarBinding {
  id: string;
  provider: string;
  auth_kind: string;
  label: string;
  account_label: string | null;
  direction: string;
  redaction: string;
  enabled: boolean;
  status: string;
  last_error: string | null;
  last_synced_at: string | null;
  consecutive_failures: number;
  options: Record<string, unknown>;
  credentials?: { slot?: string; configured?: boolean; kid?: string | null }[];
}

const STATUS_TONE: Record<string, string> = {
  ok: 'text-emerald-600 dark:text-emerald-400',
  degraded: 'text-amber-600 dark:text-amber-400',
  failing: 'text-red-600 dark:text-red-400',
  stale: 'text-gray-500 dark:text-gray-400',
  'needs-auth': 'text-amber-600 dark:text-amber-400',
  'needs-secret': 'text-amber-600 dark:text-amber-400',
  unbound: 'text-amber-600 dark:text-amber-400',
};

const inputClass =
  'w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100';

/** The auth kind a provider binds with, in preference order (the catalog's
 *  accepted kinds drive the picker; this only picks the default). */
function defaultAuthKind(provider: CalendarProvider): string {
  if (provider.authKinds.includes('oauth2')) return 'oauth2';
  if (provider.authKinds.includes('app-password')) return 'app-password';
  if (provider.authKinds.includes('token')) return 'token';
  return provider.authKinds[0] ?? 'none';
}

interface SetupField {
  key: string;
  kind: 'url' | 'text' | 'secret' | 'calendar';
  required: boolean;
  labelKey: string;
  placeholder?: string | null;
  autocomplete?: string | null;
  authKind?: string | null;
  manualKey?: string | null;
}

/**
 * The bind form renders ONLY from the catalog's `setupFields` (ADR-0052 §11).
 * A field scoped to an auth kind appears only when that kind is chosen.
 */
function visibleFields(
  provider: CalendarProvider,
  authKind: string,
): SetupField[] {
  return (provider.setupFields ?? []).filter(
    (field) => !field.authKind || field.authKind === authKind,
  );
}

const FIELD_LABELS: Record<string, string> = {
  calendarUrl: 'Calendar URL (CalDAV collection)',
  baseUrl: 'Base URL',
  feedUrl: 'ICS feed URL',
  username: 'Username',
  appPassword: 'App password',
  token: 'Token',
  calendarPicker: 'Calendar id (optional, default primary)',
};

function fieldLabel(field: SetupField): string {
  return FIELD_LABELS[field.labelKey] ?? field.labelKey;
}

function bindingNeedsSecret(binding: CalendarBinding): boolean {
  if (binding.auth_kind === 'oauth2') {
    return binding.status === 'unbound' || binding.status === 'needs-auth';
  }
  if (binding.auth_kind === 'none') return false;
  return binding.status === 'unbound' || binding.status === 'needs-secret';
}

export const CloudCalendarsCard: React.FC = () => {
  const { t } = useTranslation();
  const { currentPatient } = usePatientStore();
  const [catalog, setCatalog] = useState<CalendarCatalog | null>(null);
  const [bindings, setBindings] = useState<CalendarBinding[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [providerId, setProviderId] = useState('');
  const [label, setLabel] = useState('');
  const [direction, setDirection] = useState('push');
  const [redaction, setRedaction] = useState('neutral');
  const [consentFull, setConsentFull] = useState(false);
  const [authKind, setAuthKind] = useState('');
  // Bind-form values keyed by the catalog's setupFields `key`. The form is
  // rendered from data (ADR-0052 §11 / Rev 6) — a new provider that speaks an
  // existing protocol needs no change in this file.
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});

  const provider = useMemo(
    () => catalog?.providers.find((row) => row.id === providerId) ?? null,
    [catalog, providerId],
  );

  const chosenAuthKind = provider ? (authKind && provider.authKinds.includes(authKind) ? authKind : defaultAuthKind(provider)) : '';
  // E12: the consent notice is keyed to the CHOSEN auth kind, never the
  // catalog row's first kind.
  const consentNotice =
    provider?.consentNotices?.[chosenAuthKind] ?? provider?.consentNotice ?? '';

  const load = useCallback(async () => {
    if (!currentPatient) return;
    setLoading(true);
    try {
      const [catalogResponse, bindingResponse] = await Promise.all([
        api.get<CalendarCatalog>('/calendar/providers'),
        api.get<CalendarBinding[]>('/calendar/bindings', {
          params: { patient_id: currentPatient.id },
        }),
      ]);
      setCatalog(catalogResponse.data);
      setBindings(bindingResponse.data);
      setRedaction((previous) => previous || catalogResponse.data.redactionDefault);
      setDirection((previous) => previous || catalogResponse.data.directions[0] || 'push');
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || 'Failed to load calendar settings');
    } finally {
      setLoading(false);
    }
  }, [currentPatient]);

  useEffect(() => {
    void load();
  }, [load]);

  // Default the provider selection once the catalog is in — deliberately
  // OUTSIDE the fetch effect: defaulting inside a callback that also feeds
  // the effect's dependency list loops the fetch forever.
  useEffect(() => {
    if (!providerId && catalog && catalog.providers.length > 0) {
      setProviderId(catalog.providers[0].id);
    }
  }, [catalog, providerId]);

  const resetForm = () => {
    setAdding(false);
    setLabel('');
    setFieldValues({});
    setConsentFull(false);
    setAuthKind('');
  };

  const createBinding = async () => {
    if (!currentPatient || !provider) return;
    if (redaction === 'full' && !consentFull) {
      toast.error('Confirm the redaction warning before publishing full detail.');
      return;
    }
    try {
      // Options come straight from the descriptor's non-secret fields; the
      // payload key is `manualKey ?? key` — no per-provider knowledge here.
      const options: Record<string, unknown> = {};
      for (const field of visibleFields(provider, chosenAuthKind)) {
        if (field.kind === 'secret') continue;
        const raw = (fieldValues[field.key] ?? '').trim();
        if (raw) options[field.manualKey ?? field.key] = raw;
      }
      const created = await api.post<CalendarBinding>('/calendar/bindings', {
        provider: provider.id,
        patient_id: currentPatient.id,
        auth_kind: chosenAuthKind,
        label: label.trim() || provider.label,
        direction,
        redaction,
        consent_full_details: consentFull,
        options,
      });
      const binding = created.data;
      if (chosenAuthKind === 'oauth2') {
        const started = await api.post<{ authorize_url: string }>(
          `/calendar/bindings/${binding.id}/oauth/start`,
        );
        window.location.assign(started.data.authorize_url);
        return;
      }
      if (chosenAuthKind === 'app-password') {
        await api.post(`/calendar/bindings/${binding.id}/secret`, {
          value: {
            username: (fieldValues.username ?? '').trim(),
            password: fieldValues.secret ?? '',
          },
        });
      } else if (chosenAuthKind === 'token') {
        // A private feed's URL IS the credential (ADR-0052 §9 / C4): the
        // full address is sealed in the slot, the row keeps host + path.
        const value = provider.protocol === 'ics'
          ? { url: (fieldValues.url ?? '').trim() }
          : { token: fieldValues.secret ?? '' };
        await api.post(`/calendar/bindings/${binding.id}/secret`, { value });
      }
      toast.success('Calendar bound');
      resetForm();
      await load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || 'Failed to bind this calendar');
    }
  };

  const run = async (binding: CalendarBinding, action: 'test' | 'sync') => {
    setBusyId(binding.id);
    try {
      const response = await api.post(`/calendar/bindings/${binding.id}/${action}`);
      const status = response.data?.status ?? 'ok';
      toast.success(action === 'test' ? `Connection: ${status}` : `Sync: ${status}`);
      await load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || `Calendar ${action} failed`);
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (binding: CalendarBinding) => {
    if (!window.confirm(`Disconnect “${binding.label}”? Credentials are removed; clinical records stay.`)) {
      return;
    }
    setBusyId(binding.id);
    try {
      await api.delete(`/calendar/bindings/${binding.id}`);
      toast.success('Calendar disconnected');
      await load();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || 'Failed to disconnect');
    } finally {
      setBusyId(null);
    }
  };

  if (!currentPatient) return null;

  return (
    <div className="bg-white dark:bg-dark-surface shadow-sm rounded-lg mb-8 overflow-hidden border border-gray-200 dark:border-dark-border">
      <div className="px-4 py-5 sm:px-6 border-b border-gray-200 dark:border-dark-border">
        <h3 className="text-lg leading-6 font-medium text-gray-900 dark:text-white flex items-center gap-2">
          <CalendarDays className="w-5 h-5 text-blue-600 dark:text-blue-400" aria-hidden />
          {t('calendar.cloud.title', 'Cloud calendars')}
        </h3>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          {t(
            'calendar.cloud.subtitle',
            'Publish examinations and clinical events to your own calendar. Detail is redacted by default.',
          )}
        </p>
      </div>
      <div className="px-4 py-4 sm:px-6 space-y-4">
        {loading && !catalog ? (
          <p className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden />
            Loading calendar providers…
          </p>
        ) : null}

        {adding && provider && catalog ? (
          <div className="rounded-lg border border-gray-200 dark:border-dark-border p-3 space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs space-y-1">
                <span className="text-gray-500 dark:text-gray-400">Provider</span>
                <select className={inputClass} value={providerId} onChange={(event) => setProviderId(event.target.value)}>
                  {catalog.providers.map((row) => (
                    <option key={row.id} value={row.id}>{row.label}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs space-y-1">
                <span className="text-gray-500 dark:text-gray-400">Label</span>
                <input className={inputClass} value={label} placeholder={provider.label} onChange={(event) => setLabel(event.target.value)} />
              </label>
              <label className="text-xs space-y-1">
                <span className="text-gray-500 dark:text-gray-400">Direction</span>
                <select className={inputClass} value={direction} onChange={(event) => setDirection(event.target.value)}>
                  <option value="push">Health → calendar (push)</option>
                  <option value="pull">Calendar → health (pull)</option>
                  <option value="two-way">Two-way</option>
                </select>
              </label>
              <label className="text-xs space-y-1">
                <span className="text-gray-500 dark:text-gray-400">Detail published</span>
                <select className={inputClass} value={redaction} onChange={(event) => setRedaction(event.target.value)}>
                  {catalog.redactionProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>{profile.id}</option>
                  ))}
                </select>
              </label>
              {provider.authKinds.length > 1 ? (
                <label className="text-xs space-y-1">
                  <span className="text-gray-500 dark:text-gray-400">Authentication</span>
                  <select
                    className={inputClass}
                    value={chosenAuthKind}
                    onChange={(event) => setAuthKind(event.target.value)}
                  >
                    {provider.authKinds.map((kind) => (
                      <option key={kind} value={kind}>{kind}</option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {catalog.redactionProfiles.find((row) => row.id === redaction)?.description}
            </p>
            {redaction === 'full' ? (
              <label className="flex items-start gap-2 text-xs text-red-600 dark:text-red-400">
                <input type="checkbox" checked={consentFull} onChange={(event) => setConsentFull(event.target.checked)} />
                I understand the real title, notes and location will be sent to this calendar provider.
              </label>
            ) : null}

            {/* Rev 6: the bind form is rendered from the catalog's
                setupFields — no per-provider branches (ADR-0052 §11). */}
            {visibleFields(provider, chosenAuthKind).map((field) => (
              <label key={field.key} className="block text-xs space-y-1">
                <span className="text-gray-500 dark:text-gray-400">
                  {fieldLabel(field)}
                  {field.required ? ' *' : ''}
                </span>
                <input
                  className={inputClass}
                  type={field.kind === 'secret' ? 'password' : 'text'}
                  value={fieldValues[field.key] ?? ''}
                  placeholder={field.placeholder ?? undefined}
                  autoComplete={field.autocomplete ?? undefined}
                  onChange={(event) =>
                    setFieldValues((prev) => ({ ...prev, [field.key]: event.target.value }))
                  }
                />
                {field.kind === 'secret' && field.authKind === 'token' ? (
                  <span className="text-gray-400">
                    A private feed URL is stored as a keyring secret; only host + path are kept in the row.
                  </span>
                ) : null}
              </label>
            ))}

            <p className="text-xs text-gray-500 dark:text-gray-400">{consentNotice}</p>
            <div className="flex justify-end gap-2">
              <button type="button" className="px-3 py-1.5 text-xs rounded-md border border-gray-300 dark:border-gray-600" onClick={resetForm}>
                Cancel
              </button>
              <button type="button" className="px-3 py-1.5 text-xs rounded-md bg-blue-600 text-white hover:bg-blue-700" onClick={() => void createBinding()}>
                {chosenAuthKind === 'oauth2' ? 'Save & connect' : 'Bind calendar'}
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-gray-300 dark:border-gray-600"
            onClick={() => setAdding(true)}
            disabled={!catalog}
          >
            <Plus className="w-3.5 h-3.5" aria-hidden />
            Bind a calendar
          </button>
        )}

        <div className="space-y-2">
          {bindings.map((binding) => {
            const rowProvider = catalog?.providers.find((row) => row.id === binding.provider);
            const needsSecret = bindingNeedsSecret(binding);
            return (
              <div key={binding.id} className="rounded-lg border border-gray-200 dark:border-dark-border p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-white">
                      {binding.label}
                      <span className="font-normal text-gray-500 dark:text-gray-400">
                        {' '}· {rowProvider?.label ?? binding.provider}
                      </span>
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {binding.direction} · redaction {binding.redaction} ·{' '}
                      <span className={STATUS_TONE[binding.status] ?? ''}>{binding.status}</span>
                      {binding.last_synced_at ? ` · last sync ${new Date(binding.last_synced_at).toLocaleString()}` : ''}
                    </p>
                    {binding.last_error ? (
                      <p className="text-xs text-red-600 dark:text-red-400 mt-1">{binding.last_error}</p>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-1">
                    <button type="button" className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-gray-300 dark:border-gray-600" onClick={() => void run(binding, 'test')} disabled={busyId === binding.id}>
                      <Unplug className="w-3.5 h-3.5" aria-hidden /> Test
                    </button>
                    <button type="button" className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-gray-300 dark:border-gray-600" onClick={() => void run(binding, 'sync')} disabled={busyId === binding.id}>
                      <RefreshCw className={`w-3.5 h-3.5 ${busyId === binding.id ? 'animate-spin' : ''}`} aria-hidden /> Sync
                    </button>
                    <button type="button" className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md text-red-600 dark:text-red-400" onClick={() => void remove(binding)} disabled={busyId === binding.id} aria-label="Disconnect">
                      <Trash2 className="w-3.5 h-3.5" aria-hidden />
                    </button>
                  </div>
                </div>
                {needsSecret && binding.auth_kind === 'oauth2' ? (
                  <button
                    type="button"
                    className="mt-2 px-3 py-1.5 text-xs rounded-md bg-blue-600 text-white hover:bg-blue-700"
                    onClick={async () => {
                      try {
                        const started = await api.post<{ authorize_url: string }>(`/calendar/bindings/${binding.id}/oauth/start`);
                        window.location.assign(started.data.authorize_url);
                      } catch (error: any) {
                        toast.error(error?.response?.data?.detail || 'Could not start consent');
                      }
                    }}
                  >
                    Reconnect account
                  </button>
                ) : null}
              </div>
            );
          })}
          {!loading && bindings.length === 0 ? (
            <p className="text-xs text-gray-500 dark:text-gray-400">No cloud calendar is bound yet.</p>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export default CloudCalendarsCard;
