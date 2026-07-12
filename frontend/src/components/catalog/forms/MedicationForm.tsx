/**
 * Medication create/edit form — name, description, indications, dosage,
 * side effects (list), contraindications. Mirrors `MedicationCatalogCreate`.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { RichTextField } from './RichTextField';

export const MedicationForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  const sideEffects = Array.isArray(values.side_effects)
    ? (values.side_effects as string[])
    : [];

  return (
    <div className="space-y-3">
      <Field label={t('catalogs.field_name', 'Name')}>
        <TextInput
          value={String(values.name ?? '')}
          onChange={(e) => onChange({ name: e.target.value })}
        />
      </Field>
      <RichTextField
        label={t('catalogs.field_description', 'Description')}
        value={String(values.description ?? '')}
        onChange={(html) => onChange({ description: html })}
      />
      <RichTextField
        label={t('catalogs.field_indications', 'Indications')}
        value={String(values.indications ?? '')}
        onChange={(html) => onChange({ indications: html })}
      />
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_dosage', 'Dosage info')}>
          <TextInput
            value={String(values.dosage_info ?? '')}
            onChange={(e) => onChange({ dosage_info: e.target.value })}
          />
        </Field>
        <RichTextField
          label={t('catalogs.field_contraindications', 'Contraindications')}
          value={String(values.contraindications ?? '')}
          onChange={(html) => onChange({ contraindications: html })}
          minHeight="120px"
        />
      </div>
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
          placeholder="Nausea, Headache"
        />
      </Field>
    </div>
  );
};
