import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, CircleDot, Undo2, Loader2 } from 'lucide-react';
import { toast } from 'react-toastify';
import type { SetupStep } from '../../../types/setup';
import { setStepManualComplete } from '../../../services/setupService';

interface Props {
  step: SetupStep;
  /** Entity scope for entity steps ('patient'); omit for role steps. */
  entity?: string;
  /** Entity id (required when entity is given). */
  entityId?: string;
  /** Called after a successful toggle so the parent can re-fetch. */
  onChanged?: () => void;
}

/**
 * "Mark as complete" / "Undo manual" affordance for wizard steps.
 *
 * Behaviour matrix (mirrors the backend `manually_completed` flag):
 * - step genuinely complete (evaluator) → hidden (no manual override needed)
 * - step manually completed            → "Marked manually · Undo" button
 * - step incomplete                    → "Mark as complete" button
 *
 * Fires `setStepManualComplete` then calls `onChanged` so the parent can
 * re-poll the checklist and the step flips green in-place.
 */
export const ManualCompleteToggle: React.FC<Props> = ({
  step,
  entity,
  entityId,
  onChanged,
}) => {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  // When the evaluator already says complete, there's nothing to toggle —
  // the manual override is meaningless and we hide the control entirely.
  if (step.completed && !step.manually_completed) return null;

  const handleToggle = async () => {
    const nextCompleted = !step.manually_completed; // true → mark; false → undo
    setBusy(true);
    try {
      await setStepManualComplete({
        stepId: step.id,
        completed: nextCompleted,
        entity,
        entityId,
      });
      toast.success(
        nextCompleted
          ? t('setup.manual_complete')
          : t('setup.manual_undo')
      );
      onChanged?.();
    } catch (err) {
      console.error('Failed to toggle manual completion', err);
      toast.error('Failed to update step.');
    } finally {
      setBusy(false);
    }
  };

  if (step.manually_completed) {
    return (
      <div className="flex items-center gap-3">
        <span className="inline-flex items-center gap-1.5 text-xs text-gray-500 dark:text-dark-muted">
          <CircleDot className="w-3.5 h-3.5" />
          {t('setup.manually_completed_hint', 'Marked manually as complete')}
        </span>
        <button
          type="button"
          onClick={handleToggle}
          disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-600 dark:text-dark-muted bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border/50 disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Undo2 className="w-3.5 h-3.5" />
          )}
          {t('setup.manual_undo', 'Mark as not done')}
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={handleToggle}
      disabled={busy}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-500 dark:text-dark-muted bg-transparent border border-dashed border-gray-300 dark:border-dark-border rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border/30 hover:text-gray-700 dark:hover:text-dark-text disabled:opacity-50"
      title={t('setup.manual_complete', 'Mark as complete')}
    >
      {busy ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
      ) : (
        <CheckCircle2 className="w-3.5 h-3.5" />
      )}
      {t('setup.manual_complete', 'Mark as complete')}
    </button>
  );
};

export default ManualCompleteToggle;
