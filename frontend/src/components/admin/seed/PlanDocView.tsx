import { useTranslation } from 'react-i18next';
import { AlertTriangle, ArrowUpCircle, Ban, CircleSlash, GitCompareArrows, MinusCircle, PlusCircle, Replace } from 'lucide-react';
import { Badge } from '@neuronection/assistant-ui';
import type { SeedPlanDoc, SeedPlanEntry } from '../../../services/seedSourcesService';

const ACTION_META: Record<
  string,
  { icon: typeof PlusCircle; tone: 'success' | 'warning' | 'danger' | 'secondary' | 'default' }
> = {
  create: { icon: PlusCircle, tone: 'success' },
  update: { icon: Replace, tone: 'warning' },
  noop: { icon: CircleSlash, tone: 'secondary' },
  skip: { icon: MinusCircle, tone: 'secondary' },
  conflict: { icon: Ban, tone: 'danger' },
  retire: { icon: MinusCircle, tone: 'danger' },
};

/** One plan entry row. An "update available" row (pack update + user edit)
 * is called out — it is the explicit adopt door's input. */
function PlanEntryRow({ entry, adoptKeyLabel }: { entry: SeedPlanEntry; adoptKeyLabel: string }) {
  const { t } = useTranslation();
  const meta = ACTION_META[entry.action] ?? ACTION_META.skip;
  const Icon = meta.icon;
  const frozen =
    entry.update_available &&
    (entry.conflict as { reason?: string } | null)?.reason === 'user_modified_freeze';
  return (
    <li className="flex items-start gap-2 py-1.5 text-sm border-b border-gray-100 dark:border-dark-border last:border-0">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <code className="font-mono text-xs break-all text-gray-800 dark:text-dark-text">
            {entry.seed_key ?? entry.entity_id ?? '—'}
          </code>
          <Badge variant={meta.tone}>{entry.action}</Badge>
          {frozen && (
            <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400 text-xs font-medium">
              <ArrowUpCircle className="h-3.5 w-3.5" aria-hidden />
              {t('seed_sources.plan.update_available', 'Update available — your edit is kept until you adopt')}
            </span>
          )}
        </div>
        <div className="mt-0.5 text-xs text-gray-500 dark:text-dark-muted">
          <span>{entry.stage}</span>
          {entry.conflict != null && (
            <span className="ml-2">
              {t('seed_sources.plan.conflict_reason', 'conflict')}:{' '}
              {String((entry.conflict as { reason?: string }).reason ?? '—')}
            </span>
          )}
          {entry.warnings.map((w) => (
            <span key={w} className="ml-2 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="mr-1 inline h-3 w-3" aria-hidden />
              {w}
            </span>
          ))}
        </div>
      </div>
      {frozen && adoptKeyLabel && (
        <code className="hidden font-mono text-[10px] text-gray-400 sm:block" title={adoptKeyLabel}>
          adopt: {entry.seed_key}
        </code>
      )}
    </li>
  );
}

/** The deterministic plan document (`PackPlan.to_doc()`): summary chips,
 * warnings/errors, then per-stage entries in stage order. */
export function PlanDocView({ plan }: { plan: SeedPlanDoc }) {
  const { t } = useTranslation();
  const summary = plan.summary ?? ({} as SeedPlanDoc['summary']);
  const stages = Object.entries(plan.stages ?? {}).sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="space-y-4">
      <div className="rounded-xl bg-gray-50 dark:bg-dark-bg/60 border border-gray-100 dark:border-dark-border p-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-dark-text">
          <GitCompareArrows className="h-4 w-4" aria-hidden />
          {plan.pack?.id ?? '—'} <span className="text-gray-400">v{plan.pack?.version ?? '—'}</span>
          <Badge variant="outline">{plan.pack?.scope ?? '—'}</Badge>
          <span className="text-xs font-normal text-gray-500 dark:text-dark-muted">
            {t('seed_sources.plan.priority', 'priority')} {plan.pack?.priority ?? '—'}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
          <Badge variant="success">+{summary.create ?? 0}</Badge>
          <Badge variant="warning">~{summary.update ?? 0}</Badge>
          <Badge variant="secondary">={summary.noop ?? 0}</Badge>
          <Badge variant="secondary">skip {summary.skip ?? 0}</Badge>
          <Badge variant="danger">conflict {summary.conflict ?? 0}</Badge>
          <Badge variant="danger">retire {summary.retire ?? 0}</Badge>
          {(summary.warnings ?? 0) > 0 && <Badge variant="warning">⚠ {summary.warnings}</Badge>}
          {(summary.errors ?? 0) > 0 && <Badge variant="danger">✕ {summary.errors}</Badge>}
        </div>
      </div>

      {(plan.warnings ?? []).length > 0 && (
        <ul className="space-y-1 text-xs text-amber-700 dark:text-amber-400">
          {plan.warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{w}</span>
            </li>
          ))}
        </ul>
      )}
      {(plan.errors ?? []).length > 0 && (
        <ul className="space-y-1 text-xs text-red-600 dark:text-red-400">
          {plan.errors.map((e) => (
            <li key={e}>✕ {e}</li>
          ))}
        </ul>
      )}

      {stages.map(([stage, entries]) => (
        <section key={stage}>
          <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500 dark:text-dark-muted mb-1">
            {stage} <span className="normal-case font-normal">({entries.length})</span>
          </h4>
          <ul>
            {entries.map((entry) => (
              <PlanEntryRow key={`${entry.stage}:${entry.seed_key ?? entry.entity_id}`} entry={entry} adoptKeyLabel="" />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
