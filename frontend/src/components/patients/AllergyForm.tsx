import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Plus, Save, Info, Calendar, ShieldAlert, Clock, X, AlertCircle } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { DatePicker } from '../ui/DatePicker';
import { CatalogItemPicker } from '../catalog/CatalogItemPicker';
import { searchCatalogs } from '../../services/catalogService';
import type { CatalogSelection } from '../../types/catalog';
import { LinksSection } from '../ai/hitl/LinksSection';
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
 *  - `showHeader` / `showActions` toggles so it can be embedded in a modal
 *    (default) or rendered bare inside a HITL popup
 *  - `onSubmit` payload shape designed to be commit-agnostic — the caller
 *    decides which REST endpoint to hit and whether to create a catalog row
 *    first when `is_new_catalog_entry` is true.
 */

export interface AllergyFormPrefill {
  name?: string;
  catalog_id?: string | null;
  matched?: boolean;
  is_new?: boolean;
  category?: AllergyCategory;
  typical_reactions?: string[];
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
  /** True when the user picked the "define a new catalog entry" path. The
   *  caller is expected to create the catalog row before committing the
   *  intolerance so it can link `code.catalog_id` to it. */
  is_new_catalog_entry: boolean;
  /** Snapshot of catalog-definition fields captured inline when defining a
   *  brand-new allergen. */
  description?: string;
  typical_reactions?: string[];
  /** User-edited graph links — attached to the allergy catalog entry.
   *  The caller's onSubmit persists them via createLinksFor('allergy',
   *  catalog_id, links) AFTER the intolerance is committed (or after the
   *  new catalog entry is created, if `is_new_catalog_entry` is true). */
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
  showHeader?: boolean;
  showActions?: boolean;
}

const ALLERGY_CATEGORIES: AllergyCategory[] = ['FOOD', 'MEDICATION', 'ENVIRONMENT', 'BIOLOGIC', 'OTHER'];
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
      showHeader = true,
      showActions = true,
    },
    ref,
  ) {
    const { t } = useTranslation();
    const [selection, setSelection] = useState<CatalogSelection[]>([]);
    const [newAllergenName, setNewAllergenName] = useState('');
    const [newDescription, setNewDescription] = useState('');
    const [newTypicalReactions, setNewTypicalReactions] = useState<string[]>([]);
    const [newReactionInput, setNewReactionInput] = useState('');
    const [isAddingNew, setIsAddingNew] = useState(false);
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
        setNewAllergenName('');
        setIsAddingNew(false);
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
        setNewTypicalReactions(prefill.typical_reactions || []);
        if (prefill.matched && prefill.catalog_id) {
          setSelection([{ type: 'allergy', id: prefill.catalog_id, label: prefill.name || '' }]);
          setIsAddingNew(false);
          setNewAllergenName('');
        } else if (prefill.is_new) {
          setSelection([]);
          setNewAllergenName(prefill.name || '');
          setIsAddingNew(true);
        } else if (prefill.name) {
          setNewAllergenName(prefill.name);
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
        setNewAllergenName('');
        setNewTypicalReactions([]);
        setIsAddingNew(false);
        setLinks([]);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [allergy, prefill]);

    const handleSubmit = async (e?: React.FormEvent) => {
      if (e) e.preventDefault();
      if (loading || (isAddingNew ? !newAllergenName.trim() : (!allergy && selection.length === 0))) return;
      setLoading(true);

      try {
        const resolvedName = isAddingNew
          ? newAllergenName
          : selection[0]?.label ?? newAllergenName;
        const payload: AllergyFormPayload = {
          clinical_status: formData.clinical_status,
          criticality: formData.criticality,
          verification_status: formData.verification_status,
          category: formData.category,
          code: allergy
            ? (allergy.code as { text: string; catalog_id?: string })
            : {
                text: resolvedName,
                catalog_id: isAddingNew ? undefined : selection[0]?.id,
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
          is_new_catalog_entry: isAddingNew,
          description: isAddingNew ? newDescription : undefined,
          typical_reactions: isAddingNew ? newTypicalReactions : undefined,
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

    const addReaction = () => {
      if (!newReaction.manifestation.trim()) return;
      setReactions([...reactions, { ...newReaction }]);
      setNewReaction({ manifestation: '', severity: 'MILD' });
    };

    const removeReaction = (idx: number) => setReactions(reactions.filter((_, i) => i !== idx));

    const addTypicalReaction = () => {
      const v = newReactionInput.trim();
      if (!v || newTypicalReactions.includes(v)) return;
      setNewTypicalReactions([...newTypicalReactions, v]);
      setNewReactionInput('');
    };

    const removeTypicalReaction = (v: string) =>
      setNewTypicalReactions(newTypicalReactions.filter(x => x !== v));

    return (
      <div className="flex flex-col flex-1 min-h-0">
        {showHeader && (
          <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between shrink-0 bg-white dark:bg-dark-surface">
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                <ShieldAlert className="w-6 h-6 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                  {allergy ? t('allergies.modal.update_title') : t('allergies.modal.new_title')}
                </h2>
                <p className="text-xs text-gray-500 dark:text-dark-muted font-medium uppercase tracking-widest mt-0.5">
                  {t('allergies.modal.clinical_records', 'Clinical record')}
                </p>
              </div>
            </div>
            <div className="flex items-center space-x-4">
              {!allergy && (
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
                    if (data.name && selection.length === 0 && !isAddingNew) {
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
                          setIsAddingNew(true);
                          setNewAllergenName(data.name);
                        }
                      } catch {
                        setNewAllergenName(data.name);
                      }
                    }
                  }}
                />
              )}
              {onCancel && (
                <button
                  type="button"
                  onClick={onCancel}
                  className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors"
                  aria-label={t('common.cancel')}
                >
                  <X className="w-5 h-5 text-gray-400" />
                </button>
              )}
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex-1 min-h-0 overflow-y-auto p-8 space-y-8 custom-scrollbar">
          {/* Substance selection */}
          {!allergy && (
            <div className="space-y-4">
              <label className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1 flex items-center">
                <Search className="w-3 h-3 mr-2" />
                {t('allergies.modal.select_from_catalog', 'Select from catalog')}
              </label>

              {!isAddingNew && (
                <>
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
                  />
                  {selection.length === 0 && (
                    <button
                      type="button"
                      onClick={() => setIsAddingNew(true)}
                      className="w-full text-left px-6 py-5 bg-blue-50/50 dark:bg-blue-900/10 hover:bg-blue-50 dark:hover:bg-blue-900/20 flex items-center space-x-3 text-blue-600 rounded-2xl border border-dashed border-blue-200"
                    >
                      <div className="p-2 bg-blue-600 text-white rounded-xl">
                        <Plus className="w-4 h-4" />
                      </div>
                      <div>
                        <p className="text-sm font-bold">{t('allergies.modal.define_new', 'Define a new allergen')}</p>
                        <p className="text-[10px] font-bold uppercase tracking-widest">
                          {t('allergies.modal.add_custom')}
                        </p>
                      </div>
                    </button>
                  )}
                </>
              )}

              {isAddingNew && (
                <div className="p-6 bg-blue-50/30 dark:bg-blue-900/10 rounded-2xl border border-blue-100/50 dark:border-blue-900/30 space-y-4 animate-in zoom-in-95">
                  <div className="flex items-center justify-between">
                    <h4 className="text-[10px] font-bold text-blue-600 uppercase tracking-widest">
                      {t('allergies.modal.define_new', 'Define a new allergen')}
                    </h4>
                    <div className="flex items-center gap-2">
                      <AIAssistButton
                        taskType="define_medication"
                        context={{}}
                        placeholder={t('allergies.modal.define_new', 'Define a new allergen')}
                        onSuggestedData={(data: any) => {
                          if (data.description) setNewDescription(data.description);
                          if (Array.isArray(data.typical_reactions))
                            setNewTypicalReactions(prev => [...new Set([...prev, ...data.typical_reactions])]);
                          if (data.name && !newAllergenName) setNewAllergenName(data.name);
                        }}
                      />
                      <button
                        type="button"
                        onClick={() => {
                          setIsAddingNew(false);
                          setNewAllergenName('');
                        }}
                        className="px-3 py-1.5 text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-widest hover:underline"
                      >
                        {t('medications.modal.change', 'Change')}
                      </button>
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <label className="block text-[10px] font-bold text-gray-400 uppercase mb-1.5 ml-1">
                        {t('allergies.modal.name_label', 'Name')}
                      </label>
                      <input
                        type="text"
                        placeholder={t('allergies.modal.search_placeholder')}
                        className="w-full px-4 py-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                        value={newAllergenName}
                        onChange={e => setNewAllergenName(e.target.value)}
                        autoFocus
                      />
                    </div>
                    {/* Category belongs here — it's a property of the NEW
                        catalog entry the user is defining. Hidden on the
                        existing-catalog path where the category travels with
                        the picked allergen. */}
                    <div>
                      <label className="block text-[10px] font-bold text-gray-400 uppercase mb-1.5 ml-1 flex items-center">
                        <AlertCircle className="w-3 h-3 mr-1.5" />
                        {t('allergies.modal.category_label', 'Category')}
                      </label>
                      <div className="flex flex-wrap gap-2">
                        {ALLERGY_CATEGORIES.map(c => (
                          <button
                            key={c}
                            type="button"
                            onClick={() => setFormData({ ...formData, category: c })}
                            className={`px-3 py-2 rounded-xl text-[10px] font-bold uppercase border transition-all ${
                              formData.category === c
                                ? 'bg-blue-600 border-blue-600 text-white'
                                : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border text-gray-500 hover:bg-gray-50'
                            }`}
                          >
                            {c}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <label className="block text-[10px] font-bold text-gray-400 uppercase mb-1.5 ml-1">
                        {t('allergies.modal.description_label', 'Description')}
                      </label>
                      <textarea
                        rows={2}
                        className="w-full px-4 py-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                        value={newDescription}
                        onChange={e => setNewDescription(e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] font-bold text-gray-400 uppercase mb-1.5 ml-1">
                        {t('allergies.modal.typical_reactions', 'Typical reactions')}
                      </label>
                      <div className="flex gap-2 mb-2">
                        <input
                          type="text"
                          className="flex-1 px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                          placeholder={t('allergies.modal.add_reaction_placeholder', 'Add a typical reaction')}
                          value={newReactionInput}
                          onChange={e => setNewReactionInput(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              addTypicalReaction();
                            }
                          }}
                        />
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {newTypicalReactions.map(se => (
                          <span
                            key={se}
                            className="px-2 py-1 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-lg text-[10px] font-bold flex items-center space-x-1"
                          >
                            <span>{se}</span>
                            <button
                              type="button"
                              onClick={() => removeTypicalReaction(se)}
                              className="text-gray-400 hover:text-red-500"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
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
                        ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm'
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
                          ? 'bg-red-600 text-white shadow-sm'
                          : 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm'
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
                <DatePicker value={formData.onset_date} onChange={v => setFormData({ ...formData, onset_date: v })} />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest px-1 flex items-center">
                  <Calendar className="w-3 h-3 mr-2" />
                  {t('allergies.modal.resolved_date')}
                </label>
                <DatePicker
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
                  className="flex-1 px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
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
                  className="px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
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
                    className="flex items-center space-x-2 bg-white dark:bg-dark-surface px-3 py-1.5 rounded-full border border-gray-100 dark:border-dark-border text-sm font-medium shadow-sm"
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
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
              placeholder={t('allergies.modal.notes_placeholder')}
              value={formData.note}
              onChange={e => setFormData({ ...formData, note: e.target.value })}
            />
          </div>

          {/* AI-proposed graph links attached to the catalog entry. */}
          <LinksSection srcType="allergy" value={links} onChange={setLinks} hideWhenEmpty />
        </form>

        {showActions && (
          <div className="px-8 py-6 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center shrink-0">
            {onReject && (
              <button
                type="button"
                onClick={onReject}
                disabled={loading}
                className="px-5 py-2.5 text-sm font-bold text-rose-600 hover:text-rose-700 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {rejectLabel ?? t('ai_chat.hitl.reject', 'Reject')}
              </button>
            )}
            <div className="ml-auto flex items-center space-x-4">
              {onCancel && (
                <button
                  type="button"
                  onClick={onCancel}
                  disabled={loading}
                  className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t('common.cancel')}
                </button>
              )}
              <button
                onClick={handleSubmit}
                disabled={
                  loading ||
                  (isAddingNew ? !newAllergenName.trim() : !allergy && selection.length === 0)
                }
                className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2"
              >
                {loading ? (
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
                ) : (
                  <Save className="w-4 h-4" />
                )}
                <span>
                  {submitLabel ??
                    (allergy
                      ? t('allergies.modal.update_record')
                      : t('allergies.modal.save_allergy'))}
                </span>
              </button>
            </div>
          </div>
        )}
      </div>
    );
  },
);
