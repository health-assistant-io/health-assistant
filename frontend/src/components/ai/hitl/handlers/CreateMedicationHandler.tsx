import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Pill, Calendar, Tag } from 'lucide-react';
import { TaskInfo } from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import {
  MedicationForm,
  MedicationFormPrefill,
  MedicationFormPayload,
} from '../../../patients/MedicationForm';
import { addPatientMedication } from '../../../../services/medicationService';
import { createCatalogItem } from '../../../../services/catalogService';
import { resolveHitlTask } from '../../../../services/aiAssistanceService';

function proposalToPrefill(proposed: Record<string, any> | undefined): MedicationFormPrefill {
  if (!proposed) return {};
  return {
    name: proposed.name,
    catalog_id: proposed.catalog_id,
    matched: proposed.matched,
    is_new: proposed.is_new,
    indications: proposed.indications,
    side_effects: proposed.side_effects,
    contraindications: proposed.contraindications,
    dosage_info: proposed.dosage_info,
    dosage: proposed.dosage,
    frequency_label: proposed.frequency_label,
    reason: proposed.reason,
    note: proposed.note,
    start_date: proposed.start_date,
    end_date: proposed.end_date,
    status: proposed.status,
  };
}

export function renderMedicationSummary(task: TaskInfo): React.ReactNode {
  const p = task.proposed_payload || {};
  const chips: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [];
  
  if (p.name) chips.push({ icon: Pill, label: p.name });
  if (p.dosage) chips.push({ icon: Tag, label: p.dosage });
  if (p.frequency_label) chips.push({ icon: Calendar, label: p.frequency_label });

  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.length === 0 ? null : (
        chips.map((c, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border text-[10px] font-bold text-gray-600 dark:text-dark-text"
          >
            <c.icon className="w-2.5 h-2.5 text-amber-500 dark:text-amber-400" />
            <span className="truncate max-w-[160px]">{c.label}</span>
          </span>
        ))
      )}
    </div>
  );
}

export const CreateMedicationHandler: React.FC<HitlHandlerProps> = ({ task, sessionId, onResolved, onCancel }) => {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const patientId = task.context?.patient_id as string | undefined;

  const handleConfirm = async (payload: MedicationFormPayload) => {
    setError(null);
    setSubmitting(true);
    try {
      let catalogId = payload.code.catalog_id;
      let drugName = payload.code.text;

      if (payload.is_new_catalog_entry) {
        const created = await createCatalogItem('medication', {
          name: payload.code.text,
          indications: payload.indications,
          side_effects: payload.side_effects || [],
          dosage_info: payload.dosage || undefined,
        });
        catalogId = String(created.id);
        drugName = String(created.name ?? created.id);
      }

      const commitPayload: any = {
        status: payload.status,
        dosage: payload.dosage,
        frequency: payload.frequency,
        start_date: payload.start_date,
        end_date: payload.end_date,
        reason: payload.reason,
        note: payload.note,
        code: {
          text: drugName,
          catalog_id: catalogId
        }
      };

      const created = await addPatientMedication(patientId!, commitPayload);

      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: commitPayload as unknown as Record<string, any>,
            result: { id: created.id },
          });
        } catch (resolveErr) {
          console.error('HITL resolve recording failed (write already committed)', resolveErr);
        }
      }

      onResolved({
        ...task,
        status: 'confirmed',
        resolved: {
          final_payload: commitPayload as unknown as Record<string, any>,
          result: { id: created.id },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      console.error('HITL add_medication confirm failed', e);
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

  if (!patientId) {
    return (
      <div className="p-4 text-xs text-amber-700 dark:text-amber-300 bg-amber-50/60 dark:bg-amber-900/10">
        {t('ai_chat.hitl.add_medication.error_no_patient', 'A patient context is required to add a medication.')}
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
      <MedicationForm
        patientId={patientId}
        prefill={proposalToPrefill(task.proposed_payload)}
        showHeader={false}
        showActions
        submitLabel={t('ai_chat.hitl.add_medication.confirm', 'Confirm & Add Medication')}
        onSubmit={handleConfirm}
        onCancel={onCancel}
        onReject={handleReject}
      />
    </div>
  );
};
