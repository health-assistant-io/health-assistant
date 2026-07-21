import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Save, ListTree, Tag, Info, Activity, Plus, X, ChevronDown } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { AIBadge } from '../ui/AIBadge';
import { UnitSelector } from '../ui/UnitSelector';
import biomarkerService from '../../services/biomarkerService';
import { Unit } from '../../types/biomarker';
import { RichTextEditor } from '../ui/RichTextEditor';

/**
 * Prefill shape. Keys mirror the backend `propose_define_biomarker`
 * proposed_payload 1:1 (the HITL contract). (task_type is still
 * `create_biomarker_definition` — kept stable for SDK alignment.) Also accepts
 * the shape used by the legacy `CreateBiomarkerModal` `initialName` flow.
 */
export interface BiomarkerDefinitionFormPrefill {
  name?: string;
  slug?: string;
  category?: string;
  coding_system?: string;
  code?: string;
  preferred_unit_symbol?: string;
  preferred_unit_id?: string;
  reference_range_min?: number | string | null;
  reference_range_max?: number | string | null;
  aliases?: string[];
  info?: string;
  is_telemetry?: boolean;
}

/** What the form hands back to onSubmit. Matches BiomarkerCreate. */
export interface BiomarkerDefinitionFormPayload {
  name: string;
  slug: string;
  category: string | null;
  coding_system: string;
  code: string | null;
  preferred_unit_id: string | null;
  preferred_unit_symbol?: string | null;
  reference_range_min: number | null;
  reference_range_max: number | null;
  aliases: string[];
  info: string | null;
  is_telemetry: boolean;
}

interface BiomarkerDefinitionFormProps {
  /** Optional patient context (kept for parity with sibling forms; not used
   *  by the create-definition endpoint which is tenant-scoped). */
  patientId?: string;
  prefill?: BiomarkerDefinitionFormPrefill;
  onSubmit: (payload: BiomarkerDefinitionFormPayload) => Promise<void>;
  onCancel?: () => void;
  onReject?: () => void;
  submitLabel?: string;
  rejectLabel?: string;
  /** Render the inline header (icon + title + AI assist + close). HITL hides
   *  it and uses the host modal's uniform header instead. */
  showHeader?: boolean;
  /** Render the footer action buttons (cancel/reject/submit). */
  showActions?: boolean;
}

const EMPTY_FORM = {
  name: '',
  slug: '',
  coding_system: 'loinc',
  code: '',
  category: '',
  aliases: [] as string[],
  preferred_unit_id: '',
  preferred_unit_symbol: '',
  reference_range_min: '',
  reference_range_max: '',
  info: '',
  is_telemetry: false,
};

function deriveSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export const BiomarkerDefinitionForm: React.FC<BiomarkerDefinitionFormProps> = ({
  prefill,
  onSubmit,
  onCancel,
  onReject,
  submitLabel,
  rejectLabel,
  showHeader = true,
  showActions = true,
}) => {
  const { t } = useTranslation();
  const [units, setUnits] = useState<Unit[]>([]);
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({ ...EMPTY_FORM });
  const [aliasInput, setAliasInput] = useState('');
  const [discoveredUnit, setDiscoveredUnit] = useState<string | null>(null);
  const [isCreatingUnit, setIsCreatingUnit] = useState(false);

  // Load units + hydrate from prefill (HITL proposal) or initialName on mount.
  useEffect(() => {
    biomarkerService.getUnits().then(setUnits).catch(console.error);

    if (prefill) {
      setFormData({
        name: prefill.name || '',
        slug: prefill.slug || (prefill.name ? deriveSlug(prefill.name) : ''),
        coding_system: prefill.coding_system || 'loinc',
        code: prefill.code || '',
        category: prefill.category || '',
        aliases: Array.isArray(prefill.aliases) ? [...prefill.aliases] : [],
        preferred_unit_id: prefill.preferred_unit_id || '',
        preferred_unit_symbol: prefill.preferred_unit_symbol || '',
        reference_range_min:
          prefill.reference_range_min != null && prefill.reference_range_min !== ''
            ? String(prefill.reference_range_min)
            : '',
        reference_range_max:
          prefill.reference_range_max != null && prefill.reference_range_max !== ''
            ? String(prefill.reference_range_max)
            : '',
        info: prefill.info || '',
        is_telemetry: !!prefill.is_telemetry,
      });
    } else {
      setFormData({ ...EMPTY_FORM });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleNameChange = (name: string) => {
    setFormData(prev => ({ ...prev, name, slug: deriveSlug(name) }));
  };

  const addAlias = () => {
    const newAlias = aliasInput.trim();
    if (!newAlias) return;
    setFormData(prev => (prev.aliases.includes(newAlias) ? prev : {
      ...prev,
      aliases: [...prev.aliases, newAlias],
    }));
    setAliasInput('');
  };

  const removeAlias = (alias: string) => {
    setFormData(prev => ({ ...prev, aliases: prev.aliases.filter(a => a !== alias) }));
  };

  const handleCreateDiscoveredUnit = async () => {
    if (!discoveredUnit) return;
    setIsCreatingUnit(true);
    try {
      const newUnit = await biomarkerService.createUnit({
        symbol: discoveredUnit,
        name: discoveredUnit,
        quantity_type: 'other',
      });
      setUnits(prev => [...prev, newUnit]);
      setFormData(prev => ({ ...prev, preferred_unit_id: newUnit.id, preferred_unit_symbol: '' }));
      setDiscoveredUnit(null);
    } catch (err) {
      console.error('Failed to create discovered unit', err);
    } finally {
      setIsCreatingUnit(false);
    }
  };

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!formData.name || !formData.slug || loading) return;

    setLoading(true);
    try {
      const payload: BiomarkerDefinitionFormPayload = {
        name: formData.name,
        slug: formData.slug,
        category: formData.category || null,
        coding_system: formData.coding_system,
        code: formData.code || null,
        preferred_unit_id: formData.preferred_unit_id || null,
        preferred_unit_symbol: formData.preferred_unit_symbol || null,
        reference_range_min:
          formData.reference_range_min === '' ? null : parseFloat(formData.reference_range_min),
        reference_range_max:
          formData.reference_range_max === '' ? null : parseFloat(formData.reference_range_max),
        aliases: formData.aliases,
        info: formData.info || null,
        is_telemetry: formData.is_telemetry,
      };
      await onSubmit(payload);
    } finally {
      setLoading(false);
    }
  };

  const applyAISuggestion = (data: any) => {
    let unitId = '';
    let unknownUnit: string | null = null;
    if (data.unit_symbol) {
      const match = units.find(
        u =>
          u.symbol.toLowerCase() === String(data.unit_symbol).toLowerCase() ||
          u.name.toLowerCase() === String(data.unit_symbol).toLowerCase()
      );
      if (match) unitId = match.id;
      else unknownUnit = data.unit_symbol;
    }
    setDiscoveredUnit(unknownUnit);

    setFormData(prev => {
      const updated = { ...prev };
      if (data.name) {
        updated.name = data.name;
        updated.slug = deriveSlug(data.name);
      }
      if (data.category) updated.category = data.category;
      if (unitId) {
        updated.preferred_unit_id = unitId;
        updated.preferred_unit_symbol = '';
      } else if (data.unit_symbol) {
        updated.preferred_unit_symbol = data.unit_symbol;
      }
      if (data.reference_range_min !== undefined && data.reference_range_min !== null) {
        updated.reference_range_min = String(data.reference_range_min);
      }
      if (data.reference_range_max !== undefined && data.reference_range_max !== null) {
        updated.reference_range_max = String(data.reference_range_max);
      }
      if (data.coding_system) updated.coding_system = data.coding_system;
      if (data.code) updated.code = data.code;
      if (data.info) updated.info = data.info;
      if (data.aliases && Array.isArray(data.aliases)) {
        updated.aliases = [...new Set([...prev.aliases, ...data.aliases])];
      }
      return updated;
    });
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {showHeader && (
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between shrink-0 bg-white dark:bg-dark-surface">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
              <ListTree className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                {t('examination_detail.repository.new_biomarker_title', 'New Biomarker Definition')}
              </h2>
              <p className="text-[10px] text-gray-400 font-black uppercase tracking-widest mt-0.5">
                {t('examination_detail.repository.catalog_template', 'Catalog Template')}
              </p>
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <AIAssistButton
              taskType="define_biomarker"
              context={{ initialName: formData.name }}
              placeholder={t('examination_detail.repository.ai_placeholder', 'Enter biomarker (e.g. \'Creatinine definition\')')}
              onSuggestedData={applyAISuggestion}
            />
            {onCancel && (
              <button type="button" onClick={onCancel} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            )}
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex-1 min-h-0 overflow-y-auto p-8 space-y-6 custom-scrollbar">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.repository.display_name', 'Display Name')}
            </label>
            <input
              type="text"
              placeholder={t('examination_detail.repository.display_name_placeholder', 'e.g. White Blood Cell Count')}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
              value={formData.name}
              onChange={e => handleNameChange(e.target.value)}
              required
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.repository.system_slug', 'System Slug (Unique ID)')}
            </label>
            <input
              type="text"
              placeholder="e.g. wbc-count"
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-blue-600 dark:text-blue-400 font-mono text-sm focus:ring-2 focus:ring-blue-500/20"
              value={formData.slug}
              onChange={e => setFormData(prev => ({ ...prev, slug: e.target.value }))}
              required
            />
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.repository.coding_system', 'Coding System')}
            </label>
            <div className="relative">
              <select
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 appearance-none"
                value={formData.coding_system}
                onChange={e => setFormData(prev => ({ ...prev, coding_system: e.target.value }))}
              >
                <option value="loinc">LOINC</option>
                <option value="custom">Custom</option>
              </select>
              <div className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
                <ChevronDown className="w-4 h-4 text-gray-400" />
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {formData.coding_system === 'loinc' ? 'LOINC Code' : 'Custom Code'}
            </label>
            <input
              type="text"
              placeholder={formData.coding_system === 'loinc' ? 'e.g. 6690-2' : 'e.g. CUSTOM-WBC'}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
              value={formData.code}
              onChange={e => setFormData(prev => ({ ...prev, code: e.target.value }))}
            />
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.repository.category', 'Category')}
            </label>
            <input
              type="text"
              placeholder="e.g. Hematology"
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
              value={formData.category}
              onChange={e => setFormData(prev => ({ ...prev, category: e.target.value }))}
            />
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.repository.preferred_unit', 'Preferred Unit')}
            </label>

            {discoveredUnit && (
              <div className="mb-2 p-2 bg-indigo-50 dark:bg-indigo-900/10 border border-indigo-100 dark:border-indigo-900/30 rounded-xl flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <AIBadge size="sm" label={t('examination_detail.repository.suggested_unit_label', 'Suggested Unit')} workflow="define_biomarker" />
                  <span className="text-[11px] font-bold text-indigo-700 dark:text-indigo-300 truncate">
                    {discoveredUnit}
                  </span>
                </div>
                <button
                  type="button"
                  disabled={isCreatingUnit}
                  onClick={handleCreateDiscoveredUnit}
                  className="px-2 py-1 bg-white dark:bg-dark-surface border border-indigo-200 dark:border-indigo-800 rounded-lg text-[9px] font-black uppercase text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 transition-all flex items-center gap-1"
                >
                  {isCreatingUnit ? <Activity className="w-2.5 h-2.5 animate-spin" /> : <Plus className="w-2.5 h-2.5" />}
                  <span>{t('examination_detail.repository.add_unit', 'Add Unit')}</span>
                </button>
              </div>
            )}

            <UnitSelector
              units={units}
              selectedId={formData.preferred_unit_id}
              onSelect={u => setFormData(prev => ({ ...prev, preferred_unit_id: u.id, preferred_unit_symbol: '' }))}
              onUnitsUpdated={setUnits}
            />
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.repository.reference_range_min', 'Reference Range (Min)')}
            </label>
            <input
              type="number"
              step="any"
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
              value={formData.reference_range_min}
              onChange={e => setFormData(prev => ({ ...prev, reference_range_min: e.target.value }))}
            />
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
              {t('examination_detail.repository.reference_range_max', 'Reference Range (Max)')}
            </label>
            <input
              type="number"
              step="any"
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
              value={formData.reference_range_max}
              onChange={e => setFormData(prev => ({ ...prev, reference_range_max: e.target.value }))}
            />
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">
            {t('examination_detail.repository.data_source_type', 'Data Source Type')}
          </label>
          <label className="flex items-center space-x-3 p-4 bg-gray-50 dark:bg-dark-bg rounded-xl border border-transparent hover:border-gray-200 dark:hover:border-dark-border cursor-pointer transition-colors">
            <input
              type="checkbox"
              checked={formData.is_telemetry}
              onChange={e => setFormData(prev => ({ ...prev, is_telemetry: e.target.checked }))}
              className="w-5 h-5 text-indigo-600 rounded border-gray-300 focus:ring-indigo-500"
            />
            <div className="flex flex-col">
              <span className="text-sm font-bold text-gray-900 dark:text-dark-text flex items-center gap-2">
                <Activity className="w-4 h-4 text-indigo-500" />
                {t('examination_detail.repository.is_telemetry', 'Is Telemetry / High-Frequency IoT')}
              </span>
              <span className="text-xs text-gray-500">
                {t('examination_detail.repository.is_telemetry_hint', 'Route data from smart devices directly to TimescaleDB to handle massive volumes.')}
              </span>
            </div>
          </label>
        </div>

        <div className="space-y-2">
          <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
            <Tag className="w-3 h-3 mr-2" />
            {t('examination_detail.repository.aliases', 'Aliases & Synonyms')}
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder={t('examination_detail.repository.add_synonym', 'Add synonym...')}
              className="flex-1 px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-sm"
              value={aliasInput}
              onChange={e => setAliasInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addAlias())}
            />
            <button
              type="button"
              onClick={addAlias}
              className="px-4 bg-blue-50 dark:bg-blue-900/20 text-blue-600 rounded-xl hover:bg-blue-100"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
          <div className="flex flex-wrap gap-1 mt-2">
            {formData.aliases.map(a => (
              <span key={a} className="px-2 py-1 bg-gray-100 dark:bg-dark-bg rounded-lg text-[10px] font-bold flex items-center space-x-1">
                <span>{a}</span>
                <button type="button" onClick={() => removeAlias(a)} className="text-gray-400 hover:text-red-500">
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
            <Info className="w-3 h-3 mr-2" />
            {t('examination_detail.repository.standardized_info', 'Standardized Info (Clinical Context)')}
          </label>
          <RichTextEditor
            value={formData.info}
            onChange={val => setFormData(prev => ({ ...prev, info: val }))}
            placeholder={t('examination_detail.repository.info_placeholder', 'Clinical significance, normal ranges details...')}
            minHeight="200px"
          />
        </div>
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
                className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors uppercase tracking-widest disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {t('common.cancel')}
              </button>
            )}
            <button
              type="submit"
              onClick={(e: any) => handleSubmit(e as unknown as React.FormEvent)}
              disabled={loading || !formData.name || !formData.slug}
              className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2 uppercase tracking-widest"
            >
              {loading ? (
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              <span>
                {submitLabel ?? t('examination_detail.repository.create_definition', 'Create Definition')}
              </span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
