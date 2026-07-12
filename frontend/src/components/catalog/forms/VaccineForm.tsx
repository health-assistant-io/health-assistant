/**
 * Vaccine create/edit form — slug, name, code (CVX), target diseases, dose
 * schedule, contraindications, side effects. Mirrors `VaccineCatalogCreate`.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { RichTextField } from './RichTextField';

export const VaccineForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  const targetDiseases = Array.isArray(values.target_diseases)
    ? (values.target_diseases as string[])
    : [];
  const sideEffects = Array.isArray(values.side_effects)
    ? (values.side_effects as string[])
    : [];

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_name', 'Name')}>
          <TextInput
            value={String(values.name ?? '')}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </Field>
        <Field label="Slug" hint={t('catalogs.field_slug_hint', 'Unique key (no spaces)')}>
          <TextInput
            value={String(values.slug ?? '')}
            onChange={(e) => onChange({ slug: e.target.value })}
            placeholder="e.g. mmr"
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="CVX code">
          <TextInput
            value={String(values.code ?? '')}
            onChange={(e) => onChange({ code: e.target.value })}
            placeholder="e.g. 03"
          />
        </Field>
        <Field label={t('catalogs.field_coding_system', 'Coding system')}>
          <TextInput
            value={String(values.coding_system ?? 'cvx')}
            onChange={(e) => onChange({ coding_system: e.target.value })}
          />
        </Field>
      </div>
      <RichTextField
        label={t('catalogs.field_description', 'Description')}
        value={String(values.description ?? '')}
        onChange={(html) => onChange({ description: html })}
      />
      <Field
        label={t('catalogs.field_target_diseases', 'Target diseases')}
        hint={t('catalogs.field_aliases_hint', 'Comma-separated')}
      >
        <TextInput
          value={targetDiseases.join(', ')}
          onChange={(e) =>
            onChange({
              target_diseases: e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="Measles, Mumps, Rubella"
        />
      </Field>
      <RichTextField
        label={t('catalogs.field_contraindications', 'Contraindications')}
        value={String(values.contraindications ?? '')}
        onChange={(html) => onChange({ contraindications: html })}
      />
      <Field
        label={t('catalogs.field_side_effects', 'Side effects')}
        hint={t('catalogs.field_aliases_hint', 'Comma-separated')}
      >
        <TextInput
          value={sideEffects.join(', ')}
          onChange={(e) =>
            onChange({
              side_effects: e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="Fever, Sore arm"
        />
      </Field>
    </div>
  );
};
