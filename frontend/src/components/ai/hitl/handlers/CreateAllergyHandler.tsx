import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ShieldAlert, Tag } from 'lucide-react';
import { TaskInfo } from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import {
  AllergyForm,
  AllergyFormPrefill,
  AllergyFormPayload,
} from '../../../patients/AllergyForm';
import { addPatientAllergy } from '../../../../services/allergyService';
import { createCatalogItem } from '../../../../services/catalogService';
import { resolveHitlTask } from '../../../../services/aiAssistanceService';
import {
  createLinksFor,
  selectionsToLinkInputs,
  type LinkCreateResult,
} from '../../../../services/conceptEdges';
import { LinksSection } from '../LinksSection';
import type { CatalogSelection } from '../../../../types/catalog';

/** Convert the backend's link-snapshot shape to CatalogSelection[]. */
function proposalLinksToSelections(raw: unknown): CatalogSelection[] {
  if (!Array.isArray(raw)) return [];
  const out: CatalogSelection[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const dst = (entry as any).dst;
    const relation = (entry as any).relation;
    if (!dst || !dst.type || !dst.id) continue;
    out.push({
      type: String(dst.type),
      id: String(dst.id),
      label: String(dst.label ?? ''),
      ...(relation ? { relation: String(relation) } : {}),
    });
  }
  return out;
}

function proposalToPrefill(proposed: Record<string, any> | undefined): AllergyFormPrefill {
  if (!proposed) return {};
  return {
    name: proposed.name,
    catalog_id: proposed.catalog_id,
    matched: proposed.matched,
    is_new: proposed.is_new,
    category: proposed.category,
    typical_reactions: proposed.typical_reactions,
    clinical_status: proposed.clinical_status,
    criticality: proposed.criticality,
    verification_status: proposed.verification_status,
    onset_date: proposed.onset_date,
    resolved_date: proposed.resolved_date,
    last_occurrence: proposed.last_occurrence,
    note: proposed.note,
    reactions: proposed.reactions,
    links: proposalLinksToSelections(proposed.links),
  };
}

export function renderAllergySummary(task: TaskInfo): React.ReactNode {
  const p = task.proposed_payload || {};
  const chips: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [];

  if (p.name) chips.push({ icon: ShieldAlert, label: p.name });
  if (p.criticality) chips.push({ icon: Tag, label: String(p.criticality).toLowerCase() });
  if (p.category) chips.push({ icon: AlertTriangle, label: p.category });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {chips.length === 0
          ? null
          : chips.map((c, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border text-[10px] font-bold text-gray-600 dark:text-dark-text"
              >
                <c.icon className="w-2.5 h-2.5 text-rose-500 dark:text-rose-400" />
                <span className="truncate max-w-[160px]">{c.label}</span>
              </span>
            ))}
      </div>
      {Array.isArray(p.links) && p.links.length > 0 && (
        <LinksSection
          srcType="allergy"
          value={proposalLinksToSelections(p.links)}
          onChange={() => {}}
          mode="summary"
        />
      )}
    </div>
  );
}

export const CreateAllergyHandler: React.FC<HitlHandlerProps> = ({
  task,
  sessionId,
  onResolved,
  onCancel,
}) => {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const patientId = task.context?.patient_id as string | undefined;

  const handleConfirm = async (payload: AllergyFormPayload) => {
    setError(null);
    setSubmitting(true);
    try {
      let catalogId = payload.code.catalog_id;
      let allergenName = payload.code.text;

      // If the AI's pick didn't match an existing catalog entry, the user may
      // have followed the "define new" path inside the form. Create the row
      // before recording the intolerance so we can link to it.
      if (payload.is_new_catalog_entry) {
        const created = await createCatalogItem('allergy', {
          name: payload.code.text,
          category: payload.category,
          description: payload.description,
          typical_reactions: payload.typical_reactions ?? [],
        });
        catalogId = String(created.id);
        allergenName = String(created.name ?? created.id);
      }

      const commitPayload: any = {
        clinical_status: payload.clinical_status,
        criticality: payload.criticality,
        verification_status: payload.verification_status,
        category: payload.category,
        onset_date: payload.onset_date,
        resolved_date: payload.resolved_date,
        last_occurrence: payload.last_occurrence,
        note: payload.note,
        reactions: payload.reactions,
        code: {
          text: allergenName,
          catalog_id: catalogId,
        },
      };

      const created = await addPatientAllergy(patientId!, commitPayload);

      // Persist graph links to the allergy CATALOG entry (best-effort).
      let linkResults: LinkCreateResult[] = [];
      if (payload.links.length > 0 && catalogId) {
        linkResults = await createLinksFor(
          'allergy',
          catalogId,
          selectionsToLinkInputs(payload.links),
        );
      }
      const failedLinks = linkResults.filter(r => !r.ok).length;

      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: commitPayload as unknown as Record<string, any>,
            result: {
              id: created.id,
              catalog_id: catalogId,
              links: linkResults,
              links_failed: failedLinks,
            },
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
          result: {
            id: created.id,
            catalog_id: catalogId,
            links: linkResults,
            links_failed: failedLinks,
          },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      console.error('HITL add_allergy confirm failed', e);
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
        console.error('HITL reject allergy failed', err),
      );
    }
    onResolved({ ...task, status: 'dismissed', resolved: { at: new Date().toISOString() } });
  };

  if (!patientId) {
    return (
      <div className="p-4 text-xs text-amber-700 dark:text-amber-300 bg-amber-50/60 dark:bg-amber-900/10">
        {t('ai_chat.hitl.add_allergy.error_no_patient', 'A patient context is required to add an allergy.')}
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
      <AllergyForm
        patientId={patientId}
        prefill={proposalToPrefill(task.proposed_payload)}
        showHeader={false}
        showActions
        submitLabel={t('ai_chat.hitl.add_allergy.confirm', 'Confirm & Add Allergy')}
        onSubmit={handleConfirm}
        onCancel={onCancel}
        onReject={handleReject}
      />
    </div>
  );
};
