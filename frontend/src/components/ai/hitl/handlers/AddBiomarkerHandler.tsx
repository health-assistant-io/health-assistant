import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Activity, Tag, Calendar } from 'lucide-react';
import { TaskInfo } from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import {
  AddBiomarkerForm,
  AddBiomarkerFormPrefill,
  AddBiomarkerFormPayload,
} from '../../../examinations/AddBiomarkerForm';
import { createObservation } from '../../../../services/fhirService';
import { resolveHitlTask } from '../../../../services/aiAssistanceService';

/**
 * Maps the AI-proposed payload (produced by the backend
 * propose_add_biomarker_to_examination tool) into the shape the headless
 * AddBiomarkerForm expects for prefill. Keys already match 1:1 by contract.
 */
function proposalToPrefill(proposed: Record<string, any> | undefined): AddBiomarkerFormPrefill {
  if (!proposed) return {};
  return {
    biomarker_id: proposed.biomarker_id,
    biomarker_name: proposed.biomarker_name,
    biomarker_slug: proposed.biomarker_slug,
    value: proposed.value,
    unit: proposed.unit,
    interpretation: proposed.interpretation,
    note: proposed.note,
    matched: proposed.matched,
  };
}

/** Compact, read-only summary rendered in the chat card body. */
export function renderAddBiomarkerSummary(task: TaskInfo): React.ReactNode {
  const p = task.proposed_payload || {};
  const ctx = task.context || {};
  const chips: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [];

  // Target examination (so the user can see WHICH exam when none is open in the UI).
  const examCategory = ctx.examination_category as string | undefined;
  const examDate = ctx.examination_date ? String(ctx.examination_date).split('T')[0] : undefined;
  if (examCategory || examDate) {
    chips.push({ icon: Calendar, label: [examCategory, examDate].filter(Boolean).join(' · ') });
  }

  const name = p.biomarker_name || p.biomarker_slug;
  if (name) chips.push({ icon: Activity, label: String(name) });
  if (p.value !== undefined && p.value !== null && p.value !== '') {
    const unit = p.unit ? ` ${p.unit}` : '';
    chips.push({ icon: Tag, label: `${p.value}${unit}` });
  }
  if (p.interpretation) chips.push({ icon: Tag, label: String(p.interpretation) });

  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.length === 0 ? null : (
        chips.map((c, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border text-[10px] font-bold text-gray-600 dark:text-dark-text"
          >
            <c.icon className="w-2.5 h-2.5 text-emerald-500 dark:text-emerald-400" />
            <span className="truncate max-w-[160px]">{c.label}</span>
          </span>
        ))
      )}
    </div>
  );
}

export const AddBiomarkerHandler: React.FC<HitlHandlerProps> = ({ task, sessionId, onResolved, onCancel }) => {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const patientId = task.context?.patient_id as string | undefined;
  const examinationId = task.context?.examination_id as string | undefined;
  const examCategory = task.context?.examination_category as string | undefined;
  const examDate = task.context?.examination_date
    ? String(task.context.examination_date).split('T')[0]
    : undefined;
  const examLabel = [examCategory, examDate].filter(Boolean).join(' · ') || null;

  const handleConfirm = async (observation: AddBiomarkerFormPayload) => {
    setError(null);
    setSubmitting(true);
    try {
      // 1. Commit via the canonical, validated FHIR endpoint (AI never writes).
      const created = await createObservation(observation);
      // 2. Record the outcome into the chat session for audit + agent awareness.
      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: observation as unknown as Record<string, any>,
            result: { id: created.id, biomarker_id: observation.biomarker_id },
          });
        } catch (resolveErr) {
          // The write succeeded; a failed resolve must not undo it. Log + continue.
          console.error('HITL resolve recording failed (write already committed)', resolveErr);
        }
      }
      // 3. Notify parent to swap the card to its resolved summary state (+ close modal).
      onResolved({
        ...task,
        status: 'confirmed',
        resolved: {
          final_payload: observation as unknown as Record<string, any>,
          result: { id: created.id },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      console.error('HITL add_biomarker_to_examination confirm failed', e);
      const msg =
        e?.response?.data?.detail ||
        e?.message ||
        String(t('ai_chat.hitl.error_generic', 'Failed to save. Please review and try again.'));
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = () => {
    if (submitting) return;
    // Best-effort record; the card swaps to dismissed (+ modal closes) either way.
    if (sessionId) {
      resolveHitlTask(sessionId, task.proposal_id, { status: 'dismissed' }).catch(err =>
        console.error('HITL reject record failed', err)
      );
    }
    onResolved({ ...task, status: 'dismissed', resolved: { at: new Date().toISOString() } });
  };

  if (!patientId || !examinationId) {
    return (
      <div className="p-4 text-xs text-amber-700 dark:text-amber-300 bg-amber-50/60 dark:bg-amber-900/10">
        {t('ai_chat.hitl.add_biomarker.error_no_examination', 'An active examination is required to add a biomarker.')}
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-xl border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-900/10 p-3 text-[11px] text-rose-700 dark:text-rose-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}
      {examLabel && (
        <div className="mx-4 mt-4 flex items-center gap-2 text-[11px] text-gray-500 dark:text-dark-muted">
          <Calendar className="w-3 h-3 text-emerald-500 dark:text-emerald-400 flex-shrink-0" />
          <span>
            {t('ai_chat.hitl.add_biomarker.adding_to', 'Adding to:')}{' '}
            <span className="font-semibold text-gray-700 dark:text-dark-text">{examLabel}</span>
          </span>
        </div>
      )}
      <AddBiomarkerForm
        patientId={patientId}
        examinationId={examinationId}
        prefill={proposalToPrefill(task.proposed_payload)}
        showHeader={false}
        showActions
        submitLabel={t('ai_chat.hitl.add_biomarker.confirm', 'Confirm & Add')}
        onSubmit={handleConfirm}
        onCancel={onCancel}
        onReject={handleReject}
      />
    </div>
  );
};
