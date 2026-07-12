/**
 * Generic fallback catalog form — name + description. Used by catalog types
 * without a dedicated form (e.g. anatomy, concept).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { RichTextField } from './RichTextField';

export const GenericCatalogForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <Field label={t('catalogs.field_name', 'Name')}>
        <TextInput
          value={String(values.name ?? '')}
          onChange={(e) => onChange({ name: e.target.value })}
          placeholder={t('catalogs.field_name_placeholder', 'Item name')}
        />
      </Field>
      <RichTextField
        label={t('catalogs.field_description', 'Description')}
        value={String(values.description ?? '')}
        onChange={(html) => onChange({ description: html })}
      />
    </div>
  );
};
