import React, { useState, useEffect, forwardRef, useImperativeHandle, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { Search, Info, Calendar, Clock, Plus } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { DatePicker } from '../ui/DatePicker';
import { CatalogItemPicker } from '../catalog/CatalogItemPicker';
import { searchCatalogs } from '../../services/catalogService';
import type { CatalogSelection } from '../../types/catalog';
import { LinksSection } from '../ai/hitl/LinksSection';
import { RecordFormFooter } from '../records/RecordFormFooter';
import {
  AllergyIntolerance,
  AllergyCategory,
  AllergyClinicalStatus,
  AllergyCriticality,
  ReactionSeverity,
  getCatalogAllergy,
} from '../../services/allergyService';

/**
 * Headless, reusable allergy form. Mirrors `MedicationForm`:
 *  - `forwardRef` with an imperative `submit()` handle
 *  - `prefill` for HITL proposals
 *  - the popup shell owns the header/footer; the form renders fields plus an
 *    inline `RecordFormFooter` only for the HITL card (`showActions`)
 *  - `onSubmit` payload shape designed to be commit-agnostic — the caller
 *    decides which REST endpoint to hit. The allergen itself is always an
 *    EXISTING catalog entry (definitions are created from the browse dialog).
 */

export interface AllergyFormPrefill {
  name?: string;
  catalog_id?: string | null;
  matched?: boolean;
  category?: AllergyCategory;
  clinical_status?: AllergyClinicalStatus;
  criticality?: AllergyCriticality;
  verification_status?: string;
  onset_date?: string;
  resolved_date?: string;
  last_occurrence?: string;
  note?: string;
  reactions?: Array<{ manifestation: string; severity: ReactionSeverity; date?: string | null }>;
  /** AI-proposed graph links (attached to the catalog entry, NOT the
   *  intolerance row). Persisted by the caller via createLinksFor('allergy',
   *  catalog_id, links) after the intolerance is committed. */
  links?: CatalogSelection[];
}

export interface AllergyFormPayload {
  clinical_status: AllergyClinicalStatus;
  criticality: AllergyCriticality;
  verification_status: string;
  category: AllergyCategory;
  code: { text: string; catalog_id?: string };
  onset_date?: string | null;
  resolved_date?: string | null;
  last_occurrence?: string | null;
  note: string | null;
  reactions: Array<{ manifestation: string; severity: ReactionSeverity; date?: string | null }>;
  /** User-edited graph links — attached to the allergy catalog entry.
   *  The caller's onSubmit persists them via createLinksFor('allergy',
   *  catalog_id, links) AFTER the intolerance is committed. */
  links: CatalogSelection[];
}

export interface AllergyFormHandle {
  submit: () => void;
}

interface AllergyFormProps {
  patientId?: string;
  allergy?: AllergyIntolerance;
  prefill?: AllergyFormPrefill;
  onSubmit: (payload: AllergyFormPayload) => Promise<void>;
  onCancel?: () => void;
  onReject?: () => void;
  submitLabel?: string;
  rejectLabel?: string;
  /** Owned by the shell; set as the native `<form id>` so a footer button
   *  outside the form (the popup) can submit it through the `form` attribute. */
  formId?: string;
  /** Inline footer for the HITL chat-card placement; the popup shell renders
   *  its own `RecordFormFooter` instead. */
  showActions?: boolean;
  /** Reports validity/loading to the shell so its footer can drive Save. */
  onStateChange?: (state: { canSubmit: boolean; loading: boolean }) => void;
  /**
   * One-shot programmatic pick (HITL confirm-after-create): set the picker to
   * this catalog entry. Applies when `catalog_id` CHANGES and the picker does
   * not already hold it; never re-applies after the user clears the chip; a
   * remount with the same value is a no-op (the ref seeds from the prop).
   */
  catalogSelection?: { catalog_id: string; text: string };
}

const SEVERITIES: ReactionSeverity[] = ['MILD', 'MODERATE', 'SEVERE'];

export const AllergyForm = forwardRef<AllergyFormHandle, AllergyFormProps>(
  function AllergyForm(
    {
      patientId,
      allergy,
      prefill,
      onSubmit,
      onCancel,
      onReject,
      submitLabel,
      rejectLabel,
      formId,
      showActions = true,
      onStateChange,
      catalogSelection,
    },
    ref,
  ) {
    const { t } = useTranslation();
    const [selection, setSelection] = useState<CatalogSelection[]>([]);
    const [loading, setLoading] = useState(false);
    const [links, setLinks] = useState<CatalogSelection[]>([]);

    const [formData, setFormData] = useState<{
      clinical_status: AllergyClinicalStatus;
      criticality: AllergyCriticality;
      verification_status: string;
      category: AllergyCategory;
      onset_date: string;
      resolved_date: string;
      last_occurrence: string;
      note: string;
    }>({
      clinical_status: 'ACTIVE',
      criticality: 'LOW',
      verification_status: 'confirmed',
      category: 'OTHER',
      onset_date: '',
      resolved_date: '',
      last_occurrence: '',
      note: '',
    });

    const [reactions, setReactions] = useState<
      Array<{ manifestation: string; severity: ReactionSeverity; date?: string | null }>
    >([]);
    const [newReaction, setNewReaction] = useState<{ manifestation: string; severity: ReactionSeverity }>({
      manifestation: '',
      severity: 'MILD',
    });

    useEffect(() => {
      if (allergy) {
        setFormData({
          clinical_status: allergy.clinical_status,
          criticality: allergy.criticality || 'LOW',
          verification_status: allergy.verification_status || 'confirmed',
          category: allergy.category || 'OTHER',
          onset_date: allergy.onset_date ? allergy.onset_date.split('T')[0] : '',
          resolved_date: allergy.resolved_date ? allergy.resolved_date.split('T')[0] : '',
          last_occurrence: allergy.last_occurrence ? allergy.last_occurrence.split('T')[0] : '',
          note: allergy.note || '',
        });
        setReactions(
          (allergy.reactions || []).map(r => ({
            manifestation: r.manifestation,
            severity: r.severity,
            date: r.date,
          })),
        );
        setSelection([]);
        setLinks([]);
      } else if (prefill) {
        setFormData(prev => ({
          ...prev,
          clinical_status: prefill.clinical_status || 'ACTIVE',
          criticality: prefill.criticality || 'LOW',
          verification_status: prefill.verification_status || 'confirmed',
          category: prefill.category || 'OTHER',
          onset_date: prefill.onset_date ? prefill.onset_date.split('T')[0] : '',
          resolved_date: prefill.resolved_date ? prefill.resolved_date.split('T')[0] : '',
          last_occurrence: prefill.last_occurrence ? prefill.last_occurrence.split('T')[0] : '',
          note: prefill.note || '',
        }));
        setReactions(prefill.reactions || []);
        setLinks(Array.isArray(prefill.links) ? prefill.links : []);
        if (prefill.matched && prefill.catalog_id) {
          setSelection([{ type: 'allergy', id: prefill.catalog_id, label: prefill.name || '' }]);
        }
      } else {
        setFormData({
          clinical_status: 'ACTIVE',
          criticality: 'LOW',
          verification_status: 'confirmed',
          category: 'OTHER',
          onset_date: '',
          resolved_date: '',
          last_occurrence: '',
          note: '',
        });
        setReactions([]);
        setSelection([]);
        setLinks([]);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [allergy, prefill]);

    // One-shot programmatic pick (HITL confirm-after-create). Sets only the
    // picker — the proposed fields are left untouched — but runs the SAME
    // category sync as a manual pick: the category is a property of the
    // allergen (catalog row), not the patient's reaction. The ref seeds from
    // the prop so a remount with the same value is a no-op, and the value
    // never re-applies once the user has cleared the chip.
    const appliedCatalogRef = useRef<string | null>(catalogSelection?.catalog_id ?? null);
    useEffect(() => {
      const id = catalogSelection?.catalog_id;
      if (!id || appliedCatalogRef.current === id) return;
      appliedCatalogRef.current = id;
      if (selection.some((s) => s.id === id)) return;
      setSelection([{ type: 'allergy', id, label: catalogSelection?.text ?? '' }]);
      getCatalogAllergy(id)
        .then((entry) => {
          if (entry?.category) {
            setFormData(prev => ({ ...prev, category: entry.category }));
          }
        })
        .catch(() => {
          /* leave category as-is on lookup failure */
        });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [catalogSelection]);

    const handleSubmit = async (e?: React.FormEvent) => {
      if (e) e.preventDefault();
      if (loading || (!allergy && selection.length === 0)) return;
      setLoading(true);

      try {
        const payload: AllergyFormPayload = {
          clinical_status: formData.clinical_status,
          criticality: formData.criticality,
          verification_status: formData.verification_status,
          category: formData.category,
          code: allergy
            ? (allergy.code as { text: string; catalog_id?: string })
            : {
                text: selection[0]?.label ?? '',
                catalog_id: selection[0]?.id,
              },
          onset_date: formData.onset_date ? new Date(formData.onset_date).toISOString() : null,
          resolved_date:
            formData.clinical_status === 'RESOLVED' && formData.resolved_date
              ? new Date(formData.resolved_date).toISOString()
              : null,
          last_occurrence: formData.last_occurrence
            ? new Date(formData.last_occurrence).toISOString()
            : null,
          note: formData.note || null,
          reactions,
          links,
        };

        await onSubmit(payload);
      } catch (err) {
        console.error('Failed to save allergy form', err);
      } finally {
        setLoading(false);
      }
    };

    useImperativeHandle(ref, () => ({ submit: () => handleSubmit() }));

    const canSubmit = !loading && (selection.length > 0 || Boolean(allergy));

    const reportedState = useRef('');
    useEffect(() => {
      const key = `${canSubmit}|${loading}`;
      if (reportedState.current === key) return;
      reportedState.current = key;
      onStateChange?.({ canSubmit, loading });
    }, [canSubmit, loading, onStateChange]);

    const addReaction = () => {
      if (!newReaction.manifestation.trim()) return;
      setReactions([...reactions, { ...newReaction }]);
      setNewReaction({ manifestation: '', severity: 'MILD' });
    };

    const removeReaction = (idx: number) => setReactions(reactions.filter((_, i) => i !== idx));

    return (
      <form id={formId} onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 min-h-0 overflow-y-auto p-8 space-y-8 custom-scrollbar">
          {/* Substance selection */}
          {!allergy && (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1 flex items-center">
                  <Search className="w-3 h-3 mr-2" />
                  {t('allergies.modal.select_from_catalog', 'Select from catalog')}
                </label>
                <AIAssistButton
                  taskType="fill_medication_form"
                  context={{ patientId }}
                  onSuggestedData={async (data: any) => {
                    setFormData(prev => ({
                      ...prev,
                      note: data.note || prev.note,
                      criticality:
                        (String(data.criticality || '').toUpperCase() as AllergyCriticality) ||
                        prev.criticality,
                    }));
                    if (data.name && selection.length === 0) {
                      try {
                        const resp = await searchCatalogs(data.name, { types: 'allergy', limit: 1 });
                        if (resp.results.length > 0) {
                          const hit = resp.results[0];
                          setSelection([{ type: hit.type, id: hit.id, label: hit.label }]);
                          // Sync category from the catalog entry (the category
                          // is intrinsic to the allergen, not the patient's
                          // reaction).
                          try {
                            const entry = await getCatalogAllergy(hit.id);
                            if (entry?.category) {
                              setFormData(prev => ({ ...prev, category: entry.category }));
                            }
                          } catch { /* lookup optional */ }
                        } else {
                          // Unmatched name: the picker stays empty — definitions
                          // are created from the browse dialog (Browse → New).
                          toast.info(
                            t('medications.modal.not_in_catalog', '{{name}} is not in the catalog — define it via Browse → New.', { name: data.name }),
                          );
                        }
                      } catch {
                        // A FAILED search is not an unmatched name — only an
                        // empty result may claim "not in the catalog".
                        toast.error(
                          t('catalogs.search_failed', 'Could not search the catalog — try again.'),
                        );
                      }
                    }
                  }}
                />
              </div>

              <CatalogItemPicker
                mode="single"
                allowedTypes={['allergy']}
                value={selection}
                onChange={async (next: CatalogSelection[]) => {
                  setSelection(next);
                  // The category is a property of the allergen (catalog
                  // row), NOT the patient's reaction. Auto-derive it from
                  // the picked catalog entry so the user isn't asked to
                  // re-declare what the catalog already knows.
                  const pickedId = next[0]?.id;
                  if (pickedId) {
                    try {
                      const entry = await getCatalogAllergy(pickedId);
                      if (entry?.category) {
                        setFormData(prev => ({ ...prev, category: entry.category }));
                      }
                    } catch {
                      /* leave category as-is on lookup failure */
                    }
                  }
                }}
                placeholder={t('allergies.modal.search_placeholder')}
                displayMode="cards"
                block
                allowCreate
              />
            </div>
          )}

          {allergy && (
            <div className="p-6 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-gray-100 dark:border-dark-border">
              <p className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">
                {t('allergies.modal.editing_record_for', 'Editing record for')}
              </p>
              <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">{allergy.code.text}</h3>
            </div>
          )}

          {/* Status + criticality */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-3">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">
                {t('allergies.modal.clinical_status')}
              </label>
              <div className="flex bg-gray-50 dark:bg-dark-bg p-1 rounded-xl">
                {(['ACTIVE', 'RESOLVED'] as const).map(s => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setFormData({ ...formData, clinical_status: s })}
                    className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all ${
                      formData.clinical_status === s
                        ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-xs'
                        : 'text-gray-400 dark:text-dark-muted'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-3">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1">
                {t('allergies.modal.criticality')}
              </label>
              <div className="flex bg-gray-50 dark:bg-dark-bg p-1 rounded-xl">
                {(['LOW', 'HIGH', 'UNABLE_TO_ASSESS'] as const).map(c => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setFormData({ ...formData, criticality: c })}
                    className={`flex-1 py-2 text-[10px] font-bold rounded-lg transition-all ${
                      formData.criticality === c
                        ? c === 'HIGH'
                          ? 'bg-red-600 text-white shadow-xs'
                          : 'bg-white dark:bg-dark-surface text-blue-600 shadow-xs'
                        : 'text-gray-400 dark:text-dark-muted'
                    }`}
                  >
                    {c.replace('_', ' ')}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Category — intentionally NOT shown here. The category is a
              property of the allergen (catalog row), not the patient's
              reaction. On the catalog path it auto-travels from the picked
              entry (see onChange above); on the "define new" path it lives
              inside the new-allergen panel where it belongs. */}

          {/* Timeline */}
          <div className="space-y-4">
            <div className="flex items-center space-x-2 border-b border-gray-50 dark:border-dark-border pb-2">
              <Clock className="w-4 h-4 text-blue-500" />
              <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text tracking-tight">
                {t('allergies.clinical_timeline')}
              </h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 bg-gray-50/50 dark:bg-dark-bg/30 p-6 rounded-2xl border border-gray-50 dark:border-dark-border">
              <div className="space-y-2">
                <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                  <Calendar className="w-3 h-3 mr-2" />
                  {t('allergies.modal.onset_date')}
                </label>
                <DatePicker
                  placeholder={t('common.select_date', 'Select date')} value={formData.onset_date} onChange={v => setFormData({ ...formData, onset_date: v })} />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                  <Calendar className="w-3 h-3 mr-2" />
                  {t('allergies.modal.resolved_date')}
                </label>
                <DatePicker
                  placeholder={t('common.select_date', 'Select date')}
                  disabled={formData.clinical_status !== 'RESOLVED'}
                  value={formData.resolved_date}
                  onChange={v => setFormData({ ...formData, resolved_date: v })}
                />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                  <Calendar className="w-3 h-3 mr-2" />
                  {t('allergies.modal.last_occurrence')}
                </label>
                <DatePicker
                  placeholder={t('common.select_date', 'Select date')}
                  value={formData.last_occurrence}
                  onChange={v => setFormData({ ...formData, last_occurrence: v })}
                />
              </div>
            </div>
          </div>

          {/* Reaction episodes */}
          <div className="space-y-4">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest px-1">
              {t('allergies.modal.reaction_episodes')}
            </h3>
            <div className="bg-gray-50/50 dark:bg-dark-bg/30 p-6 rounded-2xl border border-gray-50 dark:border-dark-border space-y-4">
              <div className="flex flex-col sm:flex-row gap-3">
                <input
                  type="text"
                  placeholder={t('allergies.modal.manifestation_placeholder')}
                  className="flex-1 px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text"
                  value={newReaction.manifestation}
                  onChange={e => setNewReaction({ ...newReaction, manifestation: e.target.value })}
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addReaction();
                    }
                  }}
                />
                <select
                  className="px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text"
                  value={newReaction.severity}
                  onChange={e => setNewReaction({ ...newReaction, severity: e.target.value as ReactionSeverity })}
                >
                  {SEVERITIES.map(s => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={addReaction}
                  className="p-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 shadow-md"
                  aria-label={t('common.add')}
                >
                  <Plus className="w-5 h-5" />
                </button>
              </div>

              <div className="flex flex-wrap gap-2">
                {reactions.map((r, i) => (
                  <div
                    key={i}
                    className="flex items-center space-x-2 bg-white dark:bg-dark-surface px-3 py-1.5 rounded-full border border-gray-100 dark:border-dark-border text-sm font-medium shadow-xs"
                  >
                    <span
                      className={`w-2 h-2 rounded-full ${
                        r.severity === 'SEVERE'
                          ? 'bg-red-500'
                          : r.severity === 'MODERATE'
                            ? 'bg-yellow-500'
                            : 'bg-blue-500'
                      }`}
                    />
                    <span className="text-gray-900 dark:text-dark-text">{r.manifestation}</span>
                    <button
                      type="button"
                      onClick={() => removeReaction(i)}
                      className="text-gray-400 hover:text-red-500 ml-1"
                      aria-label={`Remove ${r.manifestation}`}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Notes */}
          <div className="space-y-3">
            <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Info className="w-3 h-3 mr-2" />
              {t('allergies.clinical_notes')}
            </label>
            <textarea
              rows={3}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text"
              placeholder={t('allergies.modal.notes_placeholder')}
              value={formData.note}
              onChange={e => setFormData({ ...formData, note: e.target.value })}
            />
          </div>

          {/* AI-proposed graph links attached to the catalog entry. */}
          <LinksSection srcType="allergy" value={links} onChange={setLinks} hideWhenEmpty />
        </div>

        {showActions && (
          <div className="flex shrink-0 items-center border-t border-gray-50 bg-gray-50 px-8 py-6 dark:border-dark-border dark:bg-dark-bg/50">
            <RecordFormFooter
              formId={formId}
              canSubmit={canSubmit}
              loading={loading}
              submitLabel={
                submitLabel ??
                (allergy
                  ? t('allergies.modal.update_record')
                  : t('allergies.modal.save_allergy'))
              }
              cancelLabel={t('common.cancel')}
              onCancel={onCancel}
              rejectLabel={rejectLabel}
              onReject={onReject}
            />
          </div>
        )}
      </form>
    );
  },
);
