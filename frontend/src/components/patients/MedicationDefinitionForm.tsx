import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Save, Pill, Info, X } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';

/**
 * Prefill shape. Keys mirror the backend `propose_create_medication_definition`
 * proposed_payload 1:1 (the HITL contract).
 */
export interface MedicationDefinitionFormPrefill {
  name?: string;
  description?: string;
  indications?: string;
  dosage_info?: string;
  contraindications?: string;
  side_effects?: string[];
}

/** What the form hands back to onSubmit. Matches MedicationCatalogCreate. */
export interface MedicationDefinitionFormPayload {
  name: string;
  description: string | undefined;
  indications: string | undefined;
  dosage_info: string | undefined;
  contraindications: string | undefined;
  side_effects: string[];
}

interface MedicationDefinitionFormProps {
  prefill?: MedicationDefinitionFormPrefill;
  onSubmit: (payload: MedicationDefinitionFormPayload) => Promise<void>;
  onCancel?: () => void;
  onReject?: () => void;
  submitLabel?: string;
  rejectLabel?: string;
  /** Render the inline header (icon + title + AI assist + close). HITL hides it. */
  showHeader?: boolean;
  /** Render the footer action buttons (cancel/reject/submit). */
  showActions?: boolean;
}

const EMPTY_FORM = {
  name: '',
  description: '',
  indications: '',
  dosage_info: '',
  contraindications: '',
  side_effects_text: '',
};

export const MedicationDefinitionForm: React.FC<MedicationDefinitionFormProps> = ({
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
  const [formData, setFormData] = useState({ ...EMPTY_FORM });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (prefill) {
      setFormData({
        name: prefill.name || '',
        description: prefill.description || '',
        indications: prefill.indications || '',
        dosage_info: prefill.dosage_info || '',
        contraindications: prefill.contraindications || '',
        side_effects_text: Array.isArray(prefill.side_effects)
          ? prefill.side_effects.join(', ')
          : '',
      });
    } else {
      setFormData({ ...EMPTY_FORM });
    }
  }, [prefill]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!formData.name.trim() || loading) return;

    setLoading(true);
    try {
      const payload: MedicationDefinitionFormPayload = {
        name: formData.name.trim(),
        description: formData.description.trim() || undefined,
        indications: formData.indications.trim() || undefined,
        dosage_info: formData.dosage_info.trim() || undefined,
        contraindications: formData.contraindications.trim() || undefined,
        side_effects: formData.side_effects_text
          .split(',')
          .map(s => s.trim())
          .filter(s => s !== ''),
      };
      await onSubmit(payload);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {showHeader && (
        <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-white dark:bg-dark-surface shrink-0">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
              <Pill className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            </div>
            <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
              {t('medications.add_custom_title')}
            </h2>
          </div>
          <div className="flex items-center space-x-2">
            <AIAssistButton
              taskType="define_medication"
              context={{}}
              onSuggestedData={(data) => {
                setFormData(prev => ({
                  ...prev,
                  name: data.name || prev.name,
                  description: data.description || prev.description,
                  indications: data.indications || prev.indications,
                  dosage_info: data.dosage_info || prev.dosage_info,
                  contraindications: data.contraindications || prev.contraindications,
                  side_effects_text: data.side_effects
                    ? (Array.isArray(data.side_effects) ? data.side_effects : []).join(', ')
                    : prev.side_effects_text,
                }));
              }}
            />
            {onCancel && (
              <button onClick={onCancel} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-500" />
              </button>
            )}
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex-1 min-h-0 overflow-y-auto p-6 space-y-4 custom-scrollbar">
        <div>
          <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">
            {t('medications.name')} *
          </label>
          <input
            type="text"
            required
            placeholder="e.g. Amoxicillin"
            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            autoFocus
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">
            {t('medications.description')}
          </label>
          <textarea
            placeholder={t('medications.description_placeholder')}
            rows={3}
            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text resize-none"
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">
            {t('medications.indications')}
          </label>
          <input
            type="text"
            placeholder={t('medications.indications_placeholder')}
            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
            value={formData.indications}
            onChange={e => setFormData({ ...formData, indications: e.target.value })}
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">
            {t('medications.dosage_info')}
          </label>
          <input
            type="text"
            placeholder={t('medications.dosage_placeholder')}
            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
            value={formData.dosage_info}
            onChange={e => setFormData({ ...formData, dosage_info: e.target.value })}
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5">
            {t('medications.contraindications')}
          </label>
          <input
            type="text"
            placeholder={t('medications.contraindications_placeholder')}
            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
            value={formData.contraindications}
            onChange={e => setFormData({ ...formData, contraindications: e.target.value })}
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 dark:text-dark-muted mb-1.5 flex items-center">
            <Info className="w-3.5 h-3.5 mr-1.5 text-gray-400" />
            {t('medications.side_effects')}
          </label>
          <input
            type="text"
            placeholder={t('medications.side_effects_placeholder')}
            className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
            value={formData.side_effects_text}
            onChange={e => setFormData({ ...formData, side_effects_text: e.target.value })}
          />
          <p className="text-[11px] text-gray-400 mt-1">
            {t('medications.side_effects_hint', 'Comma-separated list.')}
          </p>
        </div>
      </form>

      {showActions && (
        <div className="px-6 py-4 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-100 dark:border-dark-border flex items-center shrink-0">
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
          <div className="ml-auto flex items-center space-x-3">
            {onCancel && (
              <button
                type="button"
                onClick={onCancel}
                disabled={loading}
                className="px-5 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {t('common.cancel')}
              </button>
            )}
            <button
              type="submit"
              onClick={(e: any) => handleSubmit(e as unknown as React.FormEvent)}
              disabled={loading || !formData.name.trim()}
              className="px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-bold flex items-center justify-center space-x-2 shadow-lg shadow-blue-200/50 dark:shadow-none disabled:opacity-50 active:scale-95"
            >
              {loading ? (
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              <span>{submitLabel ?? t('medications.create_medication')}</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
