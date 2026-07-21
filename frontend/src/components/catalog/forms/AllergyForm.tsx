/**
 * Allergy create/edit form — name, category (enum), description, typical
 * reactions (list). Mirrors `AllergyCatalogCreate`.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { RichTextField } from './RichTextField';
import { LinksSection } from '../../ai/hitl/LinksSection';
import type { CatalogSelection } from '../../../types/catalog';

const ALLERGY_CATEGORIES = ['FOOD', 'MEDICATION', 'ENVIRONMENT', 'BIOLOGIC', 'OTHER'];

export const AllergyForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  const reactions = Array.isArray(values.typical_reactions)
    ? (values.typical_reactions as string[])
    : [];
  const category = String(values.category ?? 'OTHER');

  return (
    <div className="space-y-3">
      <Field label={t('catalogs.field_name', 'Name')}>
        <TextInput
          value={String(values.name ?? '')}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </Field>
      <Field label={t('catalogs.field_category', 'Category')}>
        <select
          value={category}
          onChange={(e) => onChange({ category: e.target.value })}
          className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
        >
          {ALLERGY_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </Field>
      <RichTextField
        label={t('catalogs.field_description', 'Description')}
        value={String(values.description ?? '')}
        onChange={(html) => onChange({ description: html })}
      />
      <Field
        label={t('catalogs.field_typical_reactions', 'Typical reactions')}
        hint={t('catalogs.field_aliases_hint', 'Comma-separated')}
      >
        <TextInput
          value={reactions.join(', ')}
          onChange={(e) =>
            onChange({
              typical_reactions: e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="Hives, Itching"
        />
      </Field>

      <LinksSection
        srcType="allergy"
        value={Array.isArray(values.links) ? (values.links as CatalogSelection[]) : []}
        onChange={(next) => onChange({ links: next })}
        hideWhenEmpty
      />
    </div>
  );
};
