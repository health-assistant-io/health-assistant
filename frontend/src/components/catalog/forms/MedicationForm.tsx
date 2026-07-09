/**
 * Medication create/edit form — name, description, indications, dosage,
 * side effects (list), contraindications. Mirrors `MedicationCatalogCreate`.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput, TextArea } from './FormFields';

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
      <Field label={t('catalogs.field_description', 'Description')}>
        <TextArea
          value={String(values.description ?? '')}
          onChange={(e) => onChange({ description: e.target.value })}
          rows={2}
        />
      </Field>
      <Field label={t('catalogs.field_indications', 'Indications')}>
        <TextArea
          value={String(values.indications ?? '')}
          onChange={(e) => onChange({ indications: e.target.value })}
          rows={2}
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_dosage', 'Dosage info')}>
          <TextInput
            value={String(values.dosage_info ?? '')}
            onChange={(e) => onChange({ dosage_info: e.target.value })}
          />
        </Field>
        <Field label={t('catalogs.field_contraindications', 'Contraindications')}>
          <TextInput
            value={String(values.contraindications ?? '')}
            onChange={(e) => onChange({ contraindications: e.target.value })}
          />
        </Field>
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
