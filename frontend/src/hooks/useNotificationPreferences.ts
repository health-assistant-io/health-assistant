import { useCallback, useEffect, useState } from 'react';
import {
  notificationService,
  type NotificationKindState,
} from '../services/notificationService';

/**
 * Module-level cache of the caller's notification preferences.
 *
 * Shared across every consumer (the mute button in the detail modal, the
 * settings hub, the per-instance tab) so that a mute in one surface is
 * immediately reflected in every other — same pattern as `useLinkSchema`.
 * The first consumer to mount pays the network cost; the rest read
 * synchronously.
 */
let _cache: NotificationKindState[] | null = null;
let _fetchInFlight: Promise<NotificationKindState[]> | null = null;

async function _fetch(integrationId?: string): Promise<NotificationKindState[]> {
  if (_fetchInFlight) return _fetchInFlight;
  _fetchInFlight = notificationService
    .getPreferences(integrationId)
    .finally(() => {
      _fetchInFlight = null;
    });
  const result = await _fetchInFlight;
  if (!integrationId) {
    // Only cache the unscoped full list (the per-instance view is scoped).
    _cache = result;
  }
  return result;
}

function _patchCache(
  kindId: string,
  patch: Partial<NotificationKindState>
): void {
  if (!_cache) return;
  _cache = _cache.map((k) =>
    k.kind_id === kindId ? { ...k, ...patch } : k
  );
}

export interface UseNotificationPreferences {
  preferences: NotificationKindState[] | null;
  loading: boolean;
  error: string | null;
  fetchAll: (integrationId?: string, force?: boolean) => Promise<void>;
  /** Set a kind to enabled/disabled. Optimistically updates the cache. */
  setKind: (kindId: string, enabled: boolean) => Promise<void>;
  /** Convenience: disable a kind (the inline mute-button path). */
  mute: (kindId: string) => Promise<void>;
  /** Convenience: re-enable a kind. */
  unmute: (kindId: string) => Promise<void>;
}

/**
 * Read + mutate notification preferences by kind_id.
 *
 * Mount-time fetch is opt-in via `autoFetch` (the modal mute button doesn't
 * need the list — it reads the hint off the notification payload; only the
 * settings surfaces need the full enumeration).
 */
export function useNotificationPreferences(
  opts: { autoFetch?: boolean; integrationId?: string } = {}
): UseNotificationPreferences {
  const { autoFetch = false, integrationId } = opts;
  const [preferences, setPreferences] = useState<NotificationKindState[] | null>(
    _cache
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(
    async (iid?: string, force = false) => {
      if (!force && _cache && !iid) {
        setPreferences(_cache);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const list = await _fetch(iid ?? integrationId);
        setPreferences(iid || integrationId ? list : _cache ?? list);
      } catch (err: any) {
        setError(err?.response?.data?.detail ?? err?.message ?? 'Failed to load');
      } finally {
        setLoading(false);
      }
    },
    [integrationId]
  );

  const setKind = useCallback(async (kindId: string, enabled: boolean) => {
    const prev = _cache;
    _patchCache(kindId, { enabled });
    setPreferences(_cache);
    try {
      await notificationService.setPreference(kindId, enabled);
    } catch (err) {
      // Roll back on failure.
      _cache = prev;
      setPreferences(_cache);
      throw err;
    }
  }, []);

  const mute = useCallback(
    (kindId: string) => setKind(kindId, false),
    [setKind]
  );
  const unmute = useCallback(
    (kindId: string) => setKind(kindId, true),
    [setKind]
  );

  useEffect(() => {
    if (autoFetch) {
      void fetchAll();
    }
  }, [autoFetch, fetchAll]);

  return { preferences, loading, error, fetchAll, setKind, mute, unmute };
}
