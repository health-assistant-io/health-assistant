import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Stethoscope, Calendar, Tag } from 'lucide-react';
import { TaskInfo } from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import {
  ClinicalEventForm,
  ClinicalEventFormPrefill,
  ClinicalEventFormPayload,
} from '../../../events/ClinicalEventForm';
import { createEvent } from '../../../../services/clinicalEventService';
import { resolveHitlTask } from '../../../../services/aiAssistanceService';

/**
 * Maps the AI-proposed payload (produced by the backend propose_create_clinical_event
 * tool) into the shape the headless ClinicalEventForm expects for prefill.
 */
function proposalToPrefill(proposed: Record<string, any> | undefined): ClinicalEventFormPrefill {
  if (!proposed) return {};
  return {
    type_id: proposed.type_id,
    type_slug: proposed.type_slug,
    title: proposed.title,
    description: proposed.description,
    status: proposed.status,
    onset_date: proposed.onset_date,
    resolved_date: proposed.resolved_date,
    event_metadata: proposed.event_metadata,
    occurrences: proposed.occurrences,
    coding_system: proposed.coding_system,
    code: proposed.code,
  };
}

/** Compact, read-only summary rendered in the chat card body. */
export function renderClinicalEventSummary(task: TaskInfo): React.ReactNode {
  const p = task.proposed_payload || {};
  const chips: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [];
  const typeName = p.type_name || p.type_slug;
  if (typeName) chips.push({ icon: Stethoscope, label: typeName });
  if (p.onset_date) {
    const d = String(p.onset_date).split('T')[0];
    chips.push({ icon: Calendar, label: d });
  }
  if (p.status) chips.push({ icon: Tag, label: String(p.status) });

  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.length === 0 ? null : (
        chips.map((c, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border text-[10px] font-bold text-gray-600 dark:text-dark-text"
          >
            <c.icon className="w-2.5 h-2.5 text-indigo-500 dark:text-indigo-400" />
            <span className="truncate max-w-[160px]">{c.label}</span>
          </span>
        ))
      )}
    </div>
  );
}

export const CreateClinicalEventHandler: React.FC<HitlHandlerProps> = ({ task, sessionId, onResolved, onCancel }) => {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const patientId = task.context?.patient_id as string | undefined;

  const handleConfirm = async (payload: ClinicalEventFormPayload) => {
    setError(null);
    setSubmitting(true);
    try {
      // 1. Commit via the canonical, validated REST endpoint (AI never writes).
      const created = await createEvent(payload);
      // 2. Record the outcome into the chat session for audit + agent awareness.
      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: payload as unknown as Record<string, any>,
            result: { id: created.id, title: created.title },
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
          final_payload: payload as unknown as Record<string, any>,
          result: { id: created.id, title: created.title },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      console.error('HITL create_clinical_event confirm failed', e);
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

  if (!patientId) {
    return (
      <div className="p-4 text-xs text-amber-700 dark:text-amber-300 bg-amber-50/60 dark:bg-amber-900/10">
        {t('ai_chat.hitl.error_no_patient', 'A patient context is required to create this event.')}
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
      <ClinicalEventForm
        patientId={patientId}
        prefill={proposalToPrefill(task.proposed_payload)}
        showHeader={false}
        showActions
        submitLabel={t('ai_chat.hitl.confirm_create', 'Confirm & Create')}
        onSubmit={handleConfirm}
        onCancel={onCancel}
        onReject={handleReject}
      />
    </div>
  );
};
