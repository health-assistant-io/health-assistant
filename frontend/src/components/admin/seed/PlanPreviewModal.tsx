import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { GitCompareArrows } from 'lucide-react';
import { Button, PanelModal } from '@neuronection/assistant-ui';
import { ErrorBanner } from '@neuronection/assistant-ui';
import { Modal as AppModal } from '../../../components/ui/Modal';
import { PlanDocView } from './PlanDocView';
import {
  approveSeedVersion,
  adoptSeedUpdates,
  applySeedVersion,
  planSeedVersion,
  rejectSeedVersion,
  seedApiError,
  type SeedPlanDoc,
  type SeedSourceVersion,
} from '../../../services/seedSourcesService';

interface PlanPreviewModalProps {
  version: SeedSourceVersion | null;
  sourceId: string | null;
  canManage: boolean;
  canApprove: boolean;
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}

/** The plan/diff preview: the read-only plan (every stage entry, warnings,
 * update-available rows, conflicts) plus the version's doors — approve,
 * reject, apply (plan-only preview here never executes) and adopt. */
export function PlanPreviewModal({
  version,
  sourceId,
  canManage,
  canApprove,
  onClose,
  onChanged,
}: PlanPreviewModalProps) {
  const { t } = useTranslation();
  const [plan, setPlan] = useState<SeedPlanDoc | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  const load = useCallback(async () => {
    if (!version) return;
    setLoading(true);
    setError(null);
    try {
      setPlan(await planSeedVersion(version.id));
    } catch (err) {
      console.error('Failed to plan seed version', err);
      setError(
        seedApiError(err) ?? {
          code: 'PLAN_FAILED',
          message: t('seed_sources.plan.failed', 'Failed to build the plan preview.'),
        },
      );
    } finally {
      setLoading(false);
    }
  }, [version, t]);

  useEffect(() => {
    if (version) void load();
    else setPlan(null);
  }, [version, load]);

  if (!version) return null;

  const hasUpdateAvailable =
    plan != null &&
    Object.values(plan.stages ?? {}).some((entries) =>
      entries.some(
        (e) =>
          e.update_available &&
          (e.conflict as { reason?: string } | null)?.reason === 'user_modified_freeze',
      ),
    );

  const runAction = async (name: string, action: () => Promise<unknown>, okMessage: string) => {
    setBusy(name);
    setError(null);
    try {
      await action();
      toast.success(okMessage);
      await onChanged();
      onClose();
    } catch (err) {
      console.error(`seed action ${name} failed`, err);
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

  const handleApply = () =>
    runAction(
      'apply',
      () => applySeedVersion(version.id, true),
      t('seed_sources.plan.applied', 'Apply finished — check the run status.'),
    );

  const handleApprove = () =>
    runAction(
      'approve',
      () => approveSeedVersion(version.id),
      t('seed_sources.plan.approved', 'Version approved on its exact digest.'),
    );

  const handleReject = () =>
    runAction(
      'reject',
      () => rejectSeedVersion(version.id, rejectReason || undefined),
      t('seed_sources.plan.rejected', 'Version rejected.'),
    ).then(() => setRejectOpen(false));

  const handleAdopt = () =>
    runAction(
      'adopt',
      () => adoptSeedUpdates(sourceId ?? version.source_id),
      t('seed_sources.plan.adopted', 'Pack updates adopted — the pack content now wins.'),
    );

  return (
    <>
      <PanelModal
        open
        onOpenChange={(open) => {
          if (!open) onClose();
        }}
        title={t('seed_sources.plan.title', 'Plan preview')}
        description={
          version.pack_id
            ? `${version.pack_id} v${version.pack_version ?? '—'} · ${version.digest.slice(0, 12)}…`
            : `${t('seed_sources.digest', 'Digest')} ${version.digest.slice(0, 12)}…`
        }
        headerIcon={<GitCompareArrows className="h-5 w-5 text-blue-500" />}
        bodyClassName="max-h-[60vh] overflow-y-auto"
        footer={
          <div className="flex flex-wrap items-center justify-end gap-2">
            {hasUpdateAvailable && canManage && (
              <Button
                type="button"
                variant="outline"
                loading={busy === 'adopt'}
                disabled={busy !== null}
                onClick={handleAdopt}
              >
                {t('seed_sources.plan.adopt_all', 'Adopt updates')}
              </Button>
            )}
            {canApprove && version.status === 'pending' && (
              <>
                <Button
                  type="button"
                  variant="outline"
                  loading={busy === 'reject'}
                  disabled={busy !== null}
                  onClick={() => setRejectOpen(true)}
                >
                  {t('seed_sources.plan.reject', 'Reject')}
                </Button>
                <Button
                  type="button"
                  loading={busy === 'approve'}
                  disabled={busy !== null}
                  onClick={handleApprove}
                >
                  {t('seed_sources.plan.approve', 'Approve')}
                </Button>
              </>
            )}
            {canManage && version.status === 'approved' && (
              <Button type="button" loading={busy === 'apply'} disabled={busy !== null} onClick={handleApply}>
                {t('seed_sources.plan.apply', 'Apply')}
              </Button>
            )}
          </div>
        }
      >
        <ErrorBanner
          message={error ? `${error.code}: ${error.message}` : null}
          action={
            <Button type="button" variant="outline" onClick={() => void load()}>
              {t('common.retry', 'Retry')}
            </Button>
          }
        />
        {loading ? (
          <p className="text-sm text-gray-500 dark:text-dark-muted">
            {t('seed_sources.plan.loading', 'Planning…')}
          </p>
        ) : plan ? (
          <PlanDocView plan={plan} />
        ) : null}
      </PanelModal>

      <AppModal
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        title={t('seed_sources.plan.reject_title', 'Reject this version?')}
      >
        <div className="space-y-3">
          <label className="block text-sm text-gray-700 dark:text-dark-text">
            {t('seed_sources.plan.reject_reason', 'Reason (recorded on the version)')}
            <textarea
              className="mt-1 w-full rounded-lg border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface p-2 text-sm"
              rows={3}
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setRejectOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              type="button"
              loading={busy === 'reject'}
              disabled={busy !== null}
              onClick={handleReject}
            >
              {t('seed_sources.plan.reject', 'Reject')}
            </Button>
          </div>
        </div>
      </AppModal>
    </>
  );
}
