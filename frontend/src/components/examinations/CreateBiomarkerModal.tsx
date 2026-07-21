import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Save } from 'lucide-react';
import biomarkerService from '../../services/biomarkerService';
import { BiomarkerForm } from '../catalog/forms/BiomarkerForm';
import type { CatalogItem } from '../../types/catalog';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (newBiomarker: any) => void;
  initialName?: string;
}

/**
 * Thin portal wrapper that hosts the catalog {@link BiomarkerForm} (the single
 * source of truth for biomarker-catalog creation). Manages draft state +
 * footer actions; the form is fully controlled.
 */
export const CreateBiomarkerModal: React.FC<Props> = ({
  isOpen,
  onClose,
  onSuccess,
  initialName = '',
}) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<CatalogItem>(() => ({
    name: initialName,
    coding_system: 'loinc',
    aliases: [],
    is_telemetry: false,
  }));
  const [submitting, setSubmitting] = useState(false);

  if (!isOpen) return null;

  const handleChange = (patch: Record<string, unknown>) =>
    setDraft((prev) => ({ ...prev, ...patch }));

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const result = await biomarkerService.createBiomarker(draft as any);
      onSuccess(result);
      onClose();
    } catch (err) {
      console.error('Failed to create biomarker', err);
      // Preserve the legacy behavior: surface a blocking alert. The slug collision
      // is the most common cause.
      alert('Failed to create biomarker definition. Slug might already exist.');
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = Boolean(draft.name) && !submitting;

  return (
    <div className="fixed inset-0 z-[1100] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh]">
        <div className="flex-1 min-h-0 overflow-y-auto p-6 custom-scrollbar">
          <BiomarkerForm values={draft} onChange={handleChange} mode="create" />
        </div>
        <div className="px-6 py-4 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-100 dark:border-dark-border flex items-center justify-end space-x-3 shrink-0">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-5 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors disabled:opacity-50"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            onClick={handleSubmit}
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
                : t('common.save', 'Save')}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
};
