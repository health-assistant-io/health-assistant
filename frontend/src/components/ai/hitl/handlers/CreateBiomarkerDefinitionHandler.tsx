import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Save, Tag, Beaker } from 'lucide-react';
import { TaskInfo } from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import { BiomarkerForm } from '../../../catalog/forms/BiomarkerForm';
import biomarkerService from '../../../../services/biomarkerService';
import { resolveHitlTask } from '../../../../services/aiAssistanceService';
import {
  createLinksFor,
  selectionsToLinkInputs,
  type LinkCreateResult,
} from '../../../../services/conceptEdges';
import { LinksSection } from '../LinksSection';
import type { CatalogItem, CatalogSelection } from '../../../../types/catalog';

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

/** Build the initial form draft from the AI's proposed payload. */
function proposalToDraft(proposed: Record<string, any> | undefined): CatalogItem {
  if (!proposed) return { name: '', links: [] };
  return {
    name: proposed.name ?? '',
    slug: proposed.slug ?? '',
    coding_system: proposed.coding_system ?? 'loinc',
    code: proposed.code ?? '',
    aliases: Array.isArray(proposed.aliases) ? proposed.aliases : [],
    preferred_unit_symbol: proposed.preferred_unit_symbol ?? null,
    reference_range_min: proposed.reference_range_min ?? null,
    reference_range_max: proposed.reference_range_max ?? null,
    info: proposed.info ?? '',
    is_telemetry: Boolean(proposed.is_telemetry),
    links: proposalLinksToSelections(proposed.links),
  };
}

/** Compact, read-only summary rendered in the chat card body. */
export function renderCreateBiomarkerSummary(task: TaskInfo): React.ReactNode {
  const p = task.proposed_payload || {};
  const chips: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [];

  if (p.name) chips.push({ icon: Beaker, label: String(p.name) });
  if (p.category) chips.push({ icon: Tag, label: String(p.category) });
  if (p.preferred_unit_symbol) chips.push({ icon: Beaker, label: String(p.preferred_unit_symbol) });
  if (p.is_telemetry) chips.push({ icon: Beaker, label: 'Telemetry' });

  return (
    <div className="space-y-2">
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
      {Array.isArray(p.links) && p.links.length > 0 && (
        <LinksSection
          srcType="biomarker"
          value={proposalLinksToSelections(p.links)}
          onChange={() => {}}
          mode="summary"
        />
      )}
    </div>
  );
}

export const CreateBiomarkerDefinitionHandler: React.FC<HitlHandlerProps> = ({
  task,
  sessionId,
  onResolved,
  onCancel,
}) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<CatalogItem>(() =>
    proposalToDraft(task.proposed_payload),
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleChange = (patch: Record<string, unknown>) =>
    setDraft((prev) => ({ ...prev, ...patch }));

  const handleConfirm = async () => {
    setError(null);
    setSubmitting(true);
    try {
      // 1. Commit via the canonical, validated REST endpoint (AI never writes).
      //    Strip links — the create endpoint doesn't know about them; we
      //    persist edges separately below.
      const { links, ...createPayload } = draft;
      const created = await biomarkerService.createBiomarker(createPayload as any);

      // 2. Persist the proposed graph links (best-effort).
      const linkSelections = Array.isArray(links) ? (links as CatalogSelection[]) : [];
      let linkResults: LinkCreateResult[] = [];
      if (linkSelections.length > 0) {
        linkResults = await createLinksFor(
          'biomarker',
          String(created.id),
          selectionsToLinkInputs(linkSelections),
        );
      }
      const failedLinks = linkResults.filter((r) => !r.ok).length;

      // 3. Record the outcome into the chat session for audit + agent awareness.
      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: draft as unknown as Record<string, any>,
            result: {
              id: created.id,
              slug: created.slug,
              links: linkResults,
              links_failed: failedLinks,
            },
          });
        } catch (resolveErr) {
          // The write succeeded; a failed resolve must not undo it. Log + continue.
          console.error('HITL resolve recording failed (write already committed)', resolveErr);
        }
      }
      // 4. Notify parent to swap the card to its resolved summary state (+ close modal).
      onResolved({
        ...task,
        status: 'confirmed',
        resolved: {
          final_payload: draft as unknown as Record<string, any>,
          result: {
            id: created.id,
            slug: created.slug,
            links: linkResults,
            links_failed: failedLinks,
          },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      console.error('HITL create_biomarker_definition confirm failed', e);
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
      resolveHitlTask(sessionId, task.proposal_id, { status: 'dismissed' }).catch((err) =>
        console.error('HITL reject record failed', err),
      );
    }
    onResolved({ ...task, status: 'dismissed', resolved: { at: new Date().toISOString() } });
  };

  const canSubmit = Boolean(draft.name) && !submitting;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-xl border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-900/10 p-3 text-[11px] text-rose-700 dark:text-rose-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 custom-scrollbar">
        <BiomarkerForm values={draft} onChange={handleChange} mode="create" />
      </div>
      <div className="px-4 py-3 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-100 dark:border-dark-border flex items-center shrink-0">
        <button
          type="button"
          onClick={handleReject}
          disabled={submitting}
          className="px-5 py-2.5 text-sm font-bold text-rose-600 hover:text-rose-700 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {t('ai_chat.hitl.reject', 'Reject')}
        </button>
        <div className="ml-auto flex items-center space-x-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="px-5 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors disabled:opacity-50"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!canSubmit}
            className="px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-blue-200/50 dark:shadow-none disabled:opacity-50 active:scale-95"
          >
            {submitting ? (
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>
              {submitting
                ? t('common.saving', 'Saving…')
                : t('ai_chat.hitl.create_biomarker.confirm', 'Confirm & Create Definition')}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
};
