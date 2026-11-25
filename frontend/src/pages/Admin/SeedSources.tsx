import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import {
  DatabaseZap,
  Download,
  GitBranch,
  HardDriveUpload,
  History,
  Package,
  Plus,
  RefreshCw,
  Ban,
} from 'lucide-react';
import { Badge, Button, EmptyState } from '@neuronection/assistant-ui';
import { ErrorBanner } from '@neuronection/assistant-ui';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { AddSourceWizard } from '../../components/admin/seed/AddSourceWizard';
import { PlanPreviewModal } from '../../components/admin/seed/PlanPreviewModal';
import { RunsModal } from '../../components/admin/seed/RunsModal';
import { useAccessStore } from '../../store/slices/accessSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { downloadSeedsZip } from '../../services/seedService';
import {
  listSeedSources,
  retireSeedSource,
  revokeSeedVersion,
  seedApiError,
  setSeedSourceEnabled,
  syncSeedSource,
  type SeedSource,
  type SeedSourceVersion,
} from '../../services/seedSourcesService';

import { formatTimestamp } from '../../lib/dateFormats';
const KIND_ICON: Record<string, typeof Package> = {
  bundled: DatabaseZap,
  file: HardDriveUpload,
  git: GitBranch,
  registry: Package,
};

function shortDigest(digest: string | null | undefined): string {
  return digest ? `${digest.slice(0, 10)}…` : '—';
}

const RUN_TONE: Record<string, 'success' | 'warning' | 'danger' | 'secondary'> = {
  ok: 'success',
  partial: 'warning',
  failed: 'danger',
  conflicted: 'danger',
  running: 'secondary',
};

const VERSION_TONE: Record<string, 'success' | 'warning' | 'danger' | 'secondary'> = {
  approved: 'success',
  pending: 'warning',
  rejected: 'danger',
  revoked: 'secondary',
};

/** The seed source lane admin surface (ADR 0056): sources table + add-source
 * wizard + plan preview + per-source history. `scope` selects the surface
 * (`/admin/system/*` for the system lane, `/admin/tenant/*` for overlays) —
 * the server scopes rows per actor either way. */
export default function SeedSources({ scope }: { scope: 'system' | 'tenant' }) {
  const { t } = useTranslation();
  const can = useAccessStore((s) => s.can);
  const isSystemAdmin = useAccessStore((s) => s.roles.includes('SYSTEM_ADMIN'));
  const showConfirmation = useUIStore((s) => s.showConfirmation);

  const [sources, setSources] = useState<SeedSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [planVersion, setPlanVersion] = useState<SeedSourceVersion | null>(null);
  const [planSourceId, setPlanSourceId] = useState<string | null>(null);
  const [runsSourceId, setRunsSourceId] = useState<string | null>(null);
  const [runsOpen, setRunsOpen] = useState(false);

  const canManage = can('seed_source:manage');
  const canApprove = can('seed_source:approve');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSources(await listSeedSources());
    } catch (err) {
      console.error('Failed to load seed sources', err);
      setError(
        seedApiError(err) ?? {
          code: 'LOAD_FAILED',
          message: t('seed_sources.load_failed', 'Failed to load the seed sources.'),
        },
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = async (
    key: string,
    action: () => Promise<unknown>,
    okMessage: string,
  ) => {
    setBusy(key);
    setError(null);
    try {
      await action();
      toast.success(okMessage);
      await load();
    } catch (err) {
      console.error(`seed action ${key} failed`, err);
      setError(
        seedApiError(err) ?? {
          code: 'ACTION_FAILED',
          message: t('seed_sources.action_failed', 'The action failed.'),
        },
      );
    } finally {
      setBusy(null);
    }
  };

  const confirmAction = (opts: {
    title: string;
    message: string;
    confirmLabel: string;
    onConfirm: () => void | Promise<void>;
  }) =>
    showConfirmation({
      title: opts.title,
      message: opts.message,
      confirmLabel: opts.confirmLabel,
      cancelLabel: t('common.cancel', 'Cancel'),
      confirmVariant: 'danger',
      onConfirm: opts.onConfirm,
    });

  const handleSync = (source: SeedSource) =>
    runAction(
      `sync:${source.id}`,
      () => syncSeedSource(source.id),
      t('seed_sources.synced', 'Sync finished — a new digest lands as a pending version.'),
    );

  const handleToggle = (source: SeedSource) =>
    runAction(
      `toggle:${source.id}`,
      () => setSeedSourceEnabled(source.id, !source.enabled),
      source.enabled
        ? t('seed_sources.disabled_toast', 'Source disabled.')
        : t('seed_sources.enabled_toast', 'Source enabled.'),
    );

  const handleRetire = (source: SeedSource) =>
    confirmAction({
      title: t('seed_sources.retire_title', 'Retire this source?'),
      message: t(
        'seed_sources.retire_message',
        'Its items retire where nothing else provides them (patient-referenced rows are kept). Rows are never deleted.',
      ),
      confirmLabel: t('seed_sources.retire', 'Retire'),
      onConfirm: () =>
        runAction(
          `retire:${source.id}`,
          () => retireSeedSource(source.id),
          t('seed_sources.retired_toast', 'Source retired — its items are retired where nothing else provides them.'),
        ),
    });

  const handleRevoke = (version: SeedSourceVersion) =>
    confirmAction({
      title: t('seed_sources.revoke_title', 'Revoke this version?'),
      message: t(
        'seed_sources.revoke_message',
        'The previous approved version re-applies and restores what this version retired.',
      ),
      confirmLabel: t('seed_sources.revoke', 'Revoke'),
      onConfirm: () =>
        runAction(
          `revoke:${version.id}`,
          () => revokeSeedVersion(version.id),
          t('seed_sources.revoked_toast', 'Version revoked — the previous approved version re-applied.'),
        ),
    });

  const handleDownload = () =>
    void runAction(
      'download',
      () => downloadSeedsZip(),
      t('seed_sources.export_started', 'Seed pack download started.'),
    );

  const openPlan = (source: SeedSource, version: SeedSourceVersion) => {
    setPlanSourceId(source.id);
    setPlanVersion(version);
  };

  const openRuns = (source: SeedSource) => {
    setRunsSourceId(source.id);
    setRunsOpen(true);
  };

  /** The actionable version: a pending digest always wins (Review/Approve
   * must reach it — after the first apply, `current_version_id` alone would
   * hide every freshly synced pending version), then the applied current
   * version, then the newest known. */
  const currentVersionOf = (source: SeedSource): SeedSourceVersion | undefined =>
    source.versions.find((v) => v.status === 'pending') ??
    source.versions.find((v) => v.id === source.current_version_id) ??
    source.versions[0];

  /** The applied (approved, `is_current`) version — Plan / apply and Revoke
   * act on it even while a newer pending digest waits for review. */
  const appliedVersionOf = (source: SeedSource): SeedSourceVersion | undefined =>
    source.versions.find((v) => v.id === source.current_version_id);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('seed_sources.title', 'Seed sources')}
        subtitle={t(
          'seed_sources.subtitle',
          'Pack sources are digest-pinned, admin-approved and applied with full provenance.',
        )}
        icon={<DatabaseZap className="w-8 h-8 text-blue-500" />}
      />

      {(canManage || isSystemAdmin) && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            {canManage && (
              <Button type="button" onClick={() => setWizardOpen(true)}>
                <Plus className="mr-1.5 h-4 w-4" aria-hidden />
                {t('seed_sources.add', 'Add source')}
              </Button>
            )}
            {/* The export route requires SEED_SOURCE_READ@SYSTEM — a
             * permission a tenant admin can hold without being a system
             * admin, so the button follows the role, not `manage`. */}
            {isSystemAdmin && (
              <Button
                type="button"
                variant="outline"
                loading={busy === 'download'}
                onClick={handleDownload}
              >
                <Download className="mr-1.5 h-4 w-4" aria-hidden />
                {t('seed_sources.download', 'Download seed pack')}
              </Button>
            )}
          </div>
          <Button type="button" variant="outline" onClick={() => void load()}>
            <RefreshCw className="mr-1.5 h-4 w-4" aria-hidden />
            {t('common.refresh', 'Refresh')}
          </Button>
        </div>
      )}

      <ErrorBanner message={error ? `${error.code}: ${error.message}` : null} />

      {loading ? (
        <LoadingState variant="section" />
      ) : sources.length === 0 ? (
        <EmptyState
          icon={DatabaseZap}
          title={
            scope === 'tenant'
              ? t('seed_sources.empty_tenant', 'No tenant overlay sources yet.')
              : t('seed_sources.empty', 'No sources beyond the bundled pack yet.')
          }
          description={t(
            'seed_sources.empty_hint',
            'Register a git repo, a registry or upload a file pack to extend the catalog.',
          )}
          action={
            canManage ? (
              <Button type="button" onClick={() => setWizardOpen(true)}>
                <Plus className="mr-1.5 h-4 w-4" aria-hidden />
                {t('seed_sources.add', 'Add source')}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-gray-100 dark:border-dark-border bg-white dark:bg-dark-surface shadow-xs">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-dark-muted">
                <th className="px-4 py-3">{t('seed_sources.col_kind', 'Kind')}</th>
                <th className="px-4 py-3">{t('seed_sources.col_source', 'Source')}</th>
                <th className="px-4 py-3">{t('seed_sources.col_version', 'Version')}</th>
                <th className="px-4 py-3">{t('seed_sources.col_digest', 'Digest')}</th>
                <th className="px-4 py-3">{t('seed_sources.col_files', 'Files')}</th>
                <th className="px-4 py-3">{t('seed_sources.col_priority', 'Priority')}</th>
                <th className="px-4 py-3">{t('seed_sources.col_scope', 'Scope')}</th>
                <th className="px-4 py-3">{t('seed_sources.col_status', 'Status')}</th>
                <th className="px-4 py-3 text-right">{t('seed_sources.col_actions', 'Actions')}</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => {
                const Icon = KIND_ICON[source.kind] ?? Package;
                const version = currentVersionOf(source);
                const applied = appliedVersionOf(source);
                const latest = version?.latest_run ?? null;
                const isBundled = source.kind === 'bundled';
                return (
                  <tr
                    key={source.id}
                    className="border-t border-gray-100 dark:border-dark-border align-top"
                  >
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-1.5 capitalize">
                        <Icon className="h-4 w-4 text-gray-400" aria-hidden />
                        {source.kind}
                      </span>
                    </td>
                    <td className="px-4 py-3 max-w-56">
                      <code className="block font-mono text-xs break-all text-gray-800 dark:text-dark-text">
                        {source.uri ?? '—'}
                      </code>
                      <span className="text-xs text-gray-500 dark:text-dark-muted">
                        {[source.ref_spec, source.subpath].filter(Boolean).join(' · ') || '—'}
                      </span>
                      {source.last_error && (
                        <span className="mt-1 flex items-start gap-1 text-xs text-red-600 dark:text-red-400">
                          <Ban className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                          {source.last_error}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {version ? (
                        <div className="space-y-1">
                          {version.pack_id && (
                            <span className="block text-xs font-medium text-gray-800 dark:text-dark-text">
                              {version.pack_id} v{version.pack_version ?? '—'}
                            </span>
                          )}
                          <Badge variant={VERSION_TONE[version.status] ?? 'secondary'}>
                            {version.status}
                          </Badge>
                          {latest && (
                            <Badge variant={RUN_TONE[latest.status ?? ''] ?? 'secondary'}>
                              {latest.mode}: {latest.status}
                            </Badge>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-gray-400">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <code className="font-mono text-xs text-gray-500 dark:text-dark-muted">
                        {shortDigest(version?.digest)}
                      </code>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs">{version?.pack_files ?? '—'}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs">{source.priority}</span>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={source.scope === 'system' ? 'outline' : 'secondary'}>
                        {source.scope}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={source.enabled ? 'success' : 'secondary'}>
                        {source.enabled
                          ? t('seed_sources.enabled', 'Enabled')
                          : t('seed_sources.disabled', 'Disabled')}
                      </Badge>
                      {source.last_check_at && (
                        <span className="mt-1 block text-xs text-gray-400">
                          {t('seed_sources.last_check', 'checked')}{' '}
                          {formatTimestamp(new Date(source.last_check_at))}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap justify-end gap-1.5">
                        {!isBundled && canManage && (
                          <>
                            <Button
                              type="button"
                              variant="outline"
                              loading={busy === `sync:${source.id}`}
                              disabled={busy !== null || !source.enabled}
                              onClick={() => handleSync(source)}
                            >
                              {t('seed_sources.sync_now', 'Sync now')}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              loading={busy === `toggle:${source.id}`}
                              disabled={busy !== null}
                              onClick={() => handleToggle(source)}
                            >
                              {source.enabled
                                ? t('seed_sources.disable', 'Disable')
                                : t('seed_sources.enable', 'Enable')}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              loading={busy === `retire:${source.id}`}
                              onClick={() => handleRetire(source)}
                              disabled={busy !== null}
                            >
                              {t('seed_sources.retire', 'Retire')}
                            </Button>
                          </>
                        )}
                        {version && !isBundled && canApprove && version.status === 'pending' && (
                          <Button type="button" onClick={() => openPlan(source, version)}>
                            {t('seed_sources.review', 'Review')}
                          </Button>
                        )}
                        {applied && !isBundled && canManage && applied.status === 'approved' && (
                          <Button type="button" variant="outline" onClick={() => openPlan(source, applied)}>
                            {t('seed_sources.plan_apply', 'Plan / apply')}
                          </Button>
                        )}
                        {version && isBundled && canManage && latest?.status === 'partial' && (
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => openPlan(source, version)}
                          >
                            {t('seed_sources.reapply_partial', 'Re-apply')}
                          </Button>
                        )}
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => openRuns(source)}
                        >
                          <History className="mr-1 h-4 w-4" aria-hidden />
                          {t('seed_sources.history', 'History')}
                        </Button>
                        {applied && applied.is_current && applied.status === 'approved' && !isBundled && canApprove && (
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => handleRevoke(applied)}
                            disabled={busy !== null}
                          >
                            {t('seed_sources.revoke', 'Revoke')}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <AddSourceWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        canChooseScope={isSystemAdmin}
        onCreated={async () => {
          await load();
        }}
      />
      <PlanPreviewModal
        version={planVersion}
        sourceId={planSourceId}
        canManage={canManage}
        canApprove={canApprove}
        onClose={() => setPlanVersion(null)}
        onChanged={load}
      />
      <RunsModal
        sourceId={runsSourceId}
        open={runsOpen}
        onOpenChange={setRunsOpen}
        canManage={canManage}
        onReapply={(versionId) => {
          const source = sources.find((s) => s.versions.some((v) => v.id === versionId));
          const version = source?.versions.find((v) => v.id === versionId);
          setRunsOpen(false);
          if (source && version) openPlan(source, version);
        }}
      />
    </div>
  );
}
