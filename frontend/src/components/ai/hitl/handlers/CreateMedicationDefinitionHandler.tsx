import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Pill, Tag, Info } from 'lucide-react';
import { TaskInfo } from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import {
  MedicationDefinitionForm,
  MedicationDefinitionFormPrefill,
  MedicationDefinitionFormPayload,
} from '../../../patients/MedicationDefinitionForm';
import { addCustomMedication } from '../../../../services/medicationService';
import { resolveHitlTask } from '../../../../services/aiAssistanceService';

/**
 * Maps the AI-proposed payload (produced by the backend
 * propose_create_medication_definition tool) into the shape the headless
 * MedicationDefinitionForm expects for prefill. Keys match 1:1 by contract.
 */
function proposalToPrefill(proposed: Record<string, any> | undefined): MedicationDefinitionFormPrefill {
  if (!proposed) return {};
  return {
    name: proposed.name,
    description: proposed.description,
    indications: proposed.indications,
    dosage_info: proposed.dosage_info,
    contraindications: proposed.contraindications,
    side_effects: proposed.side_effects,
  };
}

/** Compact, read-only summary rendered in the chat card body. */
export function renderCreateMedicationSummary(task: TaskInfo): React.ReactNode {
  const p = task.proposed_payload || {};
  const chips: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [];

  if (p.name) chips.push({ icon: Pill, label: String(p.name) });
  if (p.indications) chips.push({ icon: Tag, label: String(p.indications) });
  if (Array.isArray(p.side_effects) && p.side_effects.length > 0) {
    chips.push({ icon: Info, label: `${p.side_effects.length} side effects` });
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.length === 0 ? null : (
        chips.map((c, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border text-[10px] font-bold text-gray-600 dark:text-dark-text"
          >
            <c.icon className="w-2.5 h-2.5 text-blue-500 dark:text-blue-400" />
            <span className="truncate max-w-[160px]">{c.label}</span>
          </span>
        ))
      )}
    </div>
  );
}

export const CreateMedicationDefinitionHandler: React.FC<HitlHandlerProps> = ({
  task,
  sessionId,
  onResolved,
  onCancel,
}) => {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleConfirm = async (payload: MedicationDefinitionFormPayload) => {
    setError(null);
    setSubmitting(true);
    try {
      // 1. Commit via the canonical, validated REST endpoint (AI never writes).
      const created = await addCustomMedication(payload);
      // 2. Record the outcome into the chat session for audit + agent awareness.
      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: payload as unknown as Record<string, any>,
            result: { id: created.id, name: created.name },
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
          result: { id: created.id, name: created.name },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      console.error('HITL create_medication_definition confirm failed', e);
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
    if (sessionId) {
      resolveHitlTask(sessionId, task.proposal_id, { status: 'dismissed' }).catch(err =>
        console.error('HITL reject record failed', err)
      );
    }
    onResolved({ ...task, status: 'dismissed', resolved: { at: new Date().toISOString() } });
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-xl border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-900/10 p-3 text-[11px] text-rose-700 dark:text-rose-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}
      <MedicationDefinitionForm
        prefill={proposalToPrefill(task.proposed_payload)}
        showHeader={false}
        showActions
        submitLabel={t('ai_chat.hitl.create_medication.confirm', 'Confirm & Create Definition')}
        onSubmit={handleConfirm}
        onCancel={onCancel}
        onReject={handleReject}
      />
    </div>
  );
};
