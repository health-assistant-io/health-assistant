import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../ui/Modal';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure } from '../../types/anatomy';
import type { CatalogItem } from '../../types/catalog';
import { AnatomyForm } from '../catalog/forms/AnatomyForm';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSaved: (structure: AnatomyStructure) => void;
  /** When provided, the modal edits this structure; otherwise it creates a new one. */
  structure?: AnatomyStructure | null;
}

const emptyDraft = (): CatalogItem => ({
  name: '',
  slug: '',
  class_concept_id: null,
  standard_system: null,
  standard_code: null,
  description: null,
  is_custom: true,
});

const toDraft = (s: AnatomyStructure): CatalogItem => ({
  id: String(s.id),
  name: s.name,
  slug: s.slug,
  class_concept_id: s.class_concept_id ?? null,
  class_concept_name: s.class_concept_name ?? null,
  standard_system: s.standard_system ?? null,
  standard_code: s.standard_code ?? null,
  description: s.description ?? null,
  is_custom: s.is_custom,
});

export const AnatomyStructureForm: React.FC<Props> = ({ isOpen, onClose, onSaved, structure }) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<CatalogItem>(emptyDraft());
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isEditing = !!structure;

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setDraft(structure ? toDraft(structure) : emptyDraft());
  }, [isOpen, structure]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.name?.trim() || !draft.slug?.trim()) {
      setError(t('anatomy.name_label') + ' & slug required');
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const payload = {
        name: draft.name!.trim(),
        slug: draft.slug!.trim(),
        class_concept_id: (draft.class_concept_id as string | null) ?? null,
        standard_system: (draft.standard_system ?? null) as
          | 'loinc'
          | 'snomed'
          | 'custom'
          | null,
        standard_code: (draft.standard_code as string | null) ?? null,
        description: (draft.description as string | null)?.trim() || null,
        is_custom: Boolean(draft.is_custom ?? true),
      };
      const saved = isEditing
        ? await anatomyService.update(structure!.slug, payload)
        : await anatomyService.create(payload);
      onSaved(saved);
      onClose();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : t('common.error'));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={isEditing ? t('anatomy.edit_title') : t('anatomy.add_custom_title')}
      className="max-w-lg"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <AnatomyForm
          typeMeta={{
            type: 'anatomy',
            ui: { label_key: '', icon: '', color: '', admin_route: '' },
            has_concept_link: true,
            edge_endpoint_type: 'anatomy',
            search_columns: [],
          }}
          values={draft}
          onChange={(patch) => setDraft((d) => ({ ...d, ...patch }))}
          mode={isEditing ? 'edit' : 'create'}
        />

        {error && (
          <p className="text-xs text-red-500 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded-lg">{error}</p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-bold text-gray-500 hover:text-gray-700 dark:hover:text-dark-text transition-colors"
          >
            {t('anatomy.cancel')}
          </button>
          <button
            type="submit"
            disabled={isSaving}
            className="px-4 py-2 text-sm font-bold bg-blue-500 text-white rounded-xl hover:bg-blue-600 transition-colors disabled:opacity-50"
          >
            {t('anatomy.save')}
          </button>
        </div>
      </form>
    </Modal>
  );
};
