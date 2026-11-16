import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { History } from 'lucide-react';
import { Badge, Button, EmptyState, PanelModal } from '@neuronection/assistant-ui';
import { ErrorBanner } from '@neuronection/assistant-ui';
import { PlanDocView } from './PlanDocView';
import {
  getSeedRun,
  listSeedRuns,
  seedApiError,
  type SeedPlanDoc,
  type SeedRun,
} from '../../../services/seedSourcesService';

const STATUS_TONE: Record<string, 'success' | 'warning' | 'danger' | 'secondary'> = {
  ok: 'success',
  partial: 'warning',
  failed: 'danger',
  conflicted: 'danger',
  running: 'secondary',
};

function shortDigest(digest: string | null | undefined): string {
  return digest ? `${digest.slice(0, 10)}…` : '—';
}

/** One source's run history (mode/status/timing/stats/digest); a run opens
 * into the full detail — status/stats/error, the recorded plan document and
 * the per-item outcomes. A PARTIAL apply here names exactly what failed and
 * links the re-apply door (the plan-only preview of the same version). */
export function RunsModal({
  sourceId,
  open,
  onOpenChange,
  canManage,
  onReapply,
}: {
  sourceId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
  onReapply?: (versionId: string) => void;
}) {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<SeedRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [selected, setSelected] = useState<SeedRun | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    if (!sourceId) return;
    setLoading(true);
    setError(null);
    try {
      setRuns(await listSeedRuns(sourceId));
    } catch (err) {
      console.error('Failed to load seed runs', err);
      setError(
        seedApiError(err) ?? {
          code: 'RUNS_FAILED',
          message: t('seed_sources.runs.failed', 'Failed to load the run history.'),
        },
      );
    } finally {
      setLoading(false);
    }
  }, [sourceId, t]);

  useEffect(() => {
    if (open && sourceId) void load();
    if (!open) setSelected(null);
  }, [open, sourceId, load]);

  const openDetail = async (run: SeedRun) => {
    if (!sourceId) return;
    setDetailLoading(true);
    setError(null);
    try {
      setSelected(await getSeedRun(sourceId, run.id));
    } catch (err) {
      console.error('Failed to load seed run', err);
      setError(
        seedApiError(err) ?? {
          code: 'RUN_FAILED',
          message: t('seed_sources.runs.detail_failed', 'Failed to load the run.'),
        },
      );
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <PanelModal
      open={open}
      onOpenChange={onOpenChange}
      title={t('seed_sources.runs.title', 'Run history')}
      bodyClassName="max-h-[60vh] overflow-y-auto"
      headerIcon={<History className="h-5 w-5 text-blue-500" />}
    >
      <ErrorBanner message={error ? `${error.code}: ${error.message}` : null} />
      {selected ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" onClick={() => setSelected(null)}>
              {t('seed_sources.runs.back', 'Back')}
            </Button>
            <Badge variant={STATUS_TONE[selected.status ?? ''] ?? 'secondary'}>
              {selected.status ?? '—'}
            </Badge>
            <span className="text-xs text-gray-500 dark:text-dark-muted">
              {selected.mode} · {shortDigest(selected.digest)} · {selected.started_at ?? '—'}
            </span>
          </div>
          {selected.error && (
            <p className="rounded-lg bg-red-50 dark:bg-red-900/20 p-2 text-xs text-red-700 dark:text-red-400">
              {selected.error}
            </p>
          )}
          {selected.status === 'partial' && canManage && onReapply && selected.source_version_id && (
            <div className="rounded-lg bg-amber-50 dark:bg-amber-900/20 p-2 text-xs text-amber-700 dark:text-amber-400 flex items-center justify-between gap-2">
              <span>{t('seed_sources.runs.partial_hint', 'Some items failed — re-apply retries exactly the failed ones.')}</span>
              <Button type="button" variant="outline" onClick={() => onReapply(selected.source_version_id)}>
                {t('seed_sources.runs.reapply', 'Re-apply')}
              </Button>
            </div>
          )}
          {selected.plan && Object.keys(selected.plan).length > 0 && (
            <section>
              <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500 dark:text-dark-muted mb-1">
                {t('seed_sources.runs.plan', 'Recorded plan')}
              </h4>
              <PlanDocView plan={selected.plan as SeedPlanDoc} />
            </section>
          )}
          {selected.items && selected.items.length > 0 && (
            <section>
              <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500 dark:text-dark-muted mb-1">
                {t('seed_sources.runs.items', 'Items')} ({selected.items.length})
              </h4>
              <ul className="text-xs">
                {selected.items.slice(0, 200).map((item) => (
                  <li
                    key={item.id}
                    className="flex items-center gap-2 border-b border-gray-100 dark:border-dark-border py-1"
                  >
                    <code className="font-mono break-all">{item.seed_key ?? item.entity_id ?? '—'}</code>
                    <Badge variant="secondary">{item.action}</Badge>
                    {item.conflict && Object.keys(item.conflict).length > 0 && (
                      <span className="text-amber-600 dark:text-amber-400">
                        {String((item.conflict as { reason?: string }).reason ?? 'conflict')}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      ) : loading ? (
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('seed_sources.runs.loading', 'Loading runs…')}
        </p>
      ) : runs.length === 0 ? (
        <EmptyState
          compact
          icon={History}
          title={t('seed_sources.runs.empty', 'No runs yet — sync, plan or apply first.')}
        />
      ) : (
        <ul className="text-sm">
          {runs.map((run) => (
            <li key={run.id}>
              <button
                type="button"
                onClick={() => void openDetail(run)}
                disabled={detailLoading}
                className="flex w-full items-center gap-2 border-b border-gray-100 dark:border-dark-border py-2 text-left hover:bg-gray-50 dark:hover:bg-dark-bg/60"
              >
                <Badge variant={run.mode === 'apply' ? 'default' : 'outline'}>{run.mode}</Badge>
                <Badge variant={STATUS_TONE[run.status ?? ''] ?? 'secondary'}>
                  {run.status ?? '—'}
                </Badge>
                <span className="text-xs text-gray-500 dark:text-dark-muted">
                  {shortDigest(run.digest)} · {run.started_at ?? '—'}
                </span>
                {run.status === 'partial' && (
                  <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
                    {t('seed_sources.runs.partial', 'partial')}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </PanelModal>
  );
}
