/**
 * Anatomy create/edit form — the single anatomy field set, shared by the
 * Catalogs workspace and the Anatomy Explorer's structure modal.
 *
 * Registered in ``CATALOG_FORMS`` so the catalog modal's ``getCatalogForm``
 * picks it up for ``type='anatomy'``. Writes route through ``/anatomy`` (not
 * ``/catalogs/anatomy``) via the anatomy write-target, so the class concept is
 * resolved server-side.
 *
 * Fields mirror the other catalog forms: name, slug (auto-derived on create),
 * class concept (CatalogItemPicker, anatomy_class kind), coding system + code,
 * description.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput, TextArea } from './FormFields';
import { CatalogItemPicker } from '../CatalogItemPicker';
import type { CatalogSelection } from '../../../types/catalog';

const slugify = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

const CODING_SYSTEMS = ['loinc', 'snomed', 'custom'];

const selectClass =
  'w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none';

export const AnatomyForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
  mode,
}) => {
  const { t } = useTranslation();
  const [slugTouched, setSlugTouched] = useState(mode === 'edit');

  // Reset the slug auto-derive tracking whenever the edited target changes.
  useEffect(() => {
    setSlugTouched(mode === 'edit');
  }, [mode, values.id]);

  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const patch: Record<string, unknown> = { name: e.target.value };
    if (!slugTouched) patch.slug = slugify(e.target.value);
    onChange(patch);
  };

  const classValue: CatalogSelection[] = values.class_concept_id
    ? [{
        type: 'concept',
        id: String(values.class_concept_id),
        label: String(values.class_concept_name ?? values.class_concept_id),
      }]
    : [];

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('anatomy.name_label', 'Name')}>
          <TextInput
            value={String(values.name ?? '')}
            onChange={handleNameChange}
            placeholder={t('anatomy.name_placeholder', 'e.g. Left Ventricle')}
          />
        </Field>
        {mode === 'create' ? (
          <Field
            label={t('anatomy.slug_label', 'Slug')}
            hint={t('catalogs.field_slug_hint', 'kebab-case, immutable after creation')}
          >
            <TextInput
              value={String(values.slug ?? '')}
              onChange={(e) => {
                setSlugTouched(true);
                onChange({ slug: e.target.value });
              }}
              placeholder="auto-derived from name"
            />
          </Field>
        ) : (
          <Field label={t('anatomy.slug_label', 'Slug')}>
            <TextInput value={String(values.slug ?? '')} disabled />
          </Field>
        )}
      </div>

      <Field label={t('anatomy.category_label', 'Class')}>
        <CatalogItemPicker
          mode="single"
          allowedTypes={['concept']}
          conceptKind="anatomy_class"
          value={classValue}
          onChange={(next) =>
            onChange({
              class_concept_id: next.length > 0 ? next[0].id : null,
              class_concept_name: next.length > 0 ? next[0].label : null,
            })
          }
          placeholder={t('anatomy.class_placeholder', 'Search anatomy class…')}
          block
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_coding_system', 'Coding system')}>
          <select
            value={String(values.standard_system ?? '')}
            onChange={(e) =>
              onChange({ standard_system: e.target.value || null })
            }
            className={selectClass}
          >
            <option value="">—</option>
            {CODING_SYSTEMS.map((s) => (
              <option key={s} value={s}>
                {s.toUpperCase()}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t('catalogs.field_code', 'Code')}>
          <TextInput
            value={String(values.standard_code ?? '')}
            onChange={(e) =>
              onChange({ standard_code: e.target.value || null })
            }
            placeholder="e.g. 394579002"
          />
        </Field>
      </div>

      <Field label={t('anatomy.description_label', 'Description')}>
        <TextArea
          rows={3}
          value={String(values.description ?? '')}
          onChange={(e) => onChange({ description: e.target.value || null })}
        />
      </Field>
    </div>
  );
};
