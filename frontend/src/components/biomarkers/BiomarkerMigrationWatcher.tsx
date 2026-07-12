/**
 * Self-contained migration watcher for biomarkers shown inside the Catalog
 * workspace preview. Fetches the full biomarker record (with `meta_data`),
 * polls every 3s while a migration is `in_progress` (with stall detection so
 * the Retry button surfaces even if the Celery worker died), and renders the
 * shared `MigrationProgressIndicator`.
 *
 * Polling is scoped to the currently-previewed biomarker only — the catalog
 * list itself is not polled (avoids hammering the backend for every row).
 */
import React, { useEffect, useState } from 'react';
import biomarkerService from '../../services/biomarkerService';
import { MigrationProgressIndicator } from './MigrationProgressIndicator';
import type { Biomarker } from '../../types/biomarker';

interface BiomarkerMigrationWatcherProps {
  biomarkerId: string;
  /** Optional seed (e.g. the catalog list row's meta_data) so the first paint
   *  isn't blank while the full record is loading. */
  seed?: { migration_status?: string; migration_progress?: number; migration_error?: string };
  /** Bump to force a re-fetch (e.g. after the item is saved and a migration is
   *  kicked off) without remounting. The catalog bumps its relationsRevision
   *  after every save — pass that here so the watcher picks up the new state. */
  refreshKey?: number;
  /** Notifies the parent when a poll produces a new biomarker record (so the
   *  list row can be patched with the latest meta_data). */
  onBiomarkerUpdated?: (updated: Biomarker) => void;
}

type MigrationStatus = 'in_progress' | 'completed' | 'failed';

export const BiomarkerMigrationWatcher: React.FC<BiomarkerMigrationWatcherProps> = ({
  biomarkerId,
  seed,
  refreshKey,
  onBiomarkerUpdated,
}) => {
  const [status, setStatus] = useState<MigrationStatus | undefined>(
    (seed?.migration_status as MigrationStatus | undefined) ?? undefined,
  );
  const [progress, setProgress] = useState<number>(seed?.migration_progress ?? 0);
  const [errorMessage, setErrorMessage] = useState<string | undefined>(seed?.migration_error);

  // Initial fetch + polling while migration is in progress.
  useEffect(() => {
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | undefined;
    let staleCount = 0;
    let lastProgress = progress;

    const apply = (b: Biomarker) => {
      if (cancelled) return;
      const md = b.meta_data ?? {};
      const nextStatus = md.migration_status as MigrationStatus | undefined;
      const nextProgress = md.migration_progress ?? 0;
      setStatus(nextStatus);
      setProgress(nextProgress);
      setErrorMessage(md.migration_error);
      onBiomarkerUpdated?.(b);
      return nextStatus;
    };

    (async () => {
      try {
        const fresh = await biomarkerService.getBiomarkerById(biomarkerId);
        const nextStatus = apply(fresh);
        if (nextStatus === 'in_progress') {
          interval = setInterval(async () => {
            try {
              const updated = await biomarkerService.getBiomarkerById(biomarkerId);
              const currentProgress = updated.meta_data?.migration_progress ?? 0;
              if (currentProgress === lastProgress) {
                staleCount++;
              } else {
                staleCount = 0;
                lastProgress = currentProgress;
              }
              // 30s with no movement → assume the worker died; surface retry.
              if (staleCount >= 10 && updated.meta_data?.migration_status === 'in_progress') {
                updated.meta_data = {
                  ...(updated.meta_data ?? {}),
                  migration_status: 'failed',
                  migration_error:
                    'Migration stalled. The background worker may be offline or unresponsive.',
                };
              }
              const stillInProgress = apply(updated) === 'in_progress';
              if (!stillInProgress && interval) {
                clearInterval(interval);
                interval = undefined;
              }
            } catch {
              /* keep last known state */
            }
          }, 3000);
        }
      } catch {
        /* biomarker not fetchable — nothing to show */
      }
    })();

    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [biomarkerId, refreshKey]);

  const handleRetry = async () => {
    try {
      const updated = await biomarkerService.retryMigration(biomarkerId);
      applyRetry(updated);
    } catch (e) {
      console.error('Failed to retry migration', e);
    }
  };

  const applyRetry = (b: Biomarker) => {
    const md = b.meta_data ?? {};
    setStatus(md.migration_status as MigrationStatus | undefined);
    setProgress(md.migration_progress ?? 0);
    setErrorMessage(md.migration_error);
    onBiomarkerUpdated?.(b);
  };

  if (!status || status === 'completed') return null;

  return (
    <MigrationProgressIndicator
      status={status}
      progress={progress}
      errorMessage={errorMessage}
      onRetry={handleRetry}
    />
  );
};
