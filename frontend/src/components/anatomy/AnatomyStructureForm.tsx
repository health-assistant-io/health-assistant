import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../ui/Modal';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure, AnatomyCategory } from '../../types/anatomy';
import { CATEGORY_LABELS } from '../../types/anatomy';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSaved: (structure: AnatomyStructure) => void;
  /** When provided, the modal edits this structure; otherwise it creates a new one. */
  structure?: AnatomyStructure | null;
}

const slugify = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

const CATEGORIES: AnatomyCategory[] = [
  'SYSTEM',
  'REGION',
  'ORGAN',
  'ORGAN_PART',
  'TISSUE',
  'CELL',
  'SUBSTANCE',
  'JOINT',
  'OTHER',
];

const inputClass =
  'w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text';
const labelClass = 'block text-[10px] font-black uppercase text-gray-400 tracking-widest mb-1';

export const AnatomyStructureForm: React.FC<Props> = ({ isOpen, onClose, onSaved, structure }) => {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [category, setCategory] = useState<AnatomyCategory>('ORGAN');
  const [description, setDescription] = useState('');
  const [standardCode, setStandardCode] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isEditing = !!structure;

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    if (structure) {
      setName(structure.name);
      setSlug(structure.slug);
      setSlugTouched(true);
      setCategory(structure.category);
      setDescription(structure.description ?? '');
      setStandardCode(structure.standard_code ?? '');
    } else {
      setName('');
      setSlug('');
      setSlugTouched(false);
      setCategory('ORGAN');
      setDescription('');
      setStandardCode('');
    }
  }, [isOpen, structure]);

  const handleNameChange = (value: string) => {
    setName(value);
    if (!slugTouched) setSlug(slugify(value));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !slug.trim()) {
      setError(t('anatomy.name_label') + ' & slug required');
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const payload = {
        name: name.trim(),
        slug: slug.trim(),
        category,
        description: description.trim() || null,
        standard_system: standardCode.trim() ? ('snomed' as const) : null,
        standard_code: standardCode.trim() || null,
        is_custom: true,
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
        <div>
          <label className={labelClass}>{t('anatomy.name_label')}</label>
          <input
            className={inputClass}
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            autoFocus
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelClass}>{t('anatomy.slug_label')}</label>
            <input
              className={inputClass}
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugTouched(true);
              }}
            />
          </div>
          <div>
            <label className={labelClass}>{t('anatomy.category_label')}</label>
            <select
              className={inputClass}
              value={category}
              onChange={(e) => setCategory(e.target.value as AnatomyCategory)}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABELS[c]}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className={labelClass}>{t('anatomy.standard_code_label')}</label>
          <input
            className={inputClass}
            value={standardCode}
            onChange={(e) => setStandardCode(e.target.value)}
            placeholder="SNOMED CT code (optional)"
          />
        </div>

        <div>
          <label className={labelClass}>{t('anatomy.description_label')}</label>
          <textarea
            className={`${inputClass} resize-none`}
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

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
