/**
 * Medication create/edit form — name, description, indications, dosage,
 * side effects (list), contraindications. Mirrors `MedicationCatalogCreate`.
 *
 * Single source of truth for medication-catalog creation/editing. Used by:
 *  - The Catalog workspace (`/catalogs?type=medication` → New/Edit)
 *  - The HITL `propose_define_medication` handler (AI-proposed definitions)
 *
 * The form is fully controlled (no internal state). The parent owns the draft
 * and persists links via `createLinksFor('medication', id, links)` AFTER the
 * primary create returns the new id.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { RichTextField } from './RichTextField';
import { LinksSection } from '../../ai/hitl/LinksSection';
import type { CatalogSelection } from '../../../types/catalog';

export const MedicationForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  const sideEffects = Array.isArray(values.side_effects)
    ? (values.side_effects as string[])
    : [];
  const links = Array.isArray(values.links)
    ? (values.links as CatalogSelection[])
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

      {/* AI-proposed graph links (TREATS / CONTRAINDICATES / etc.).
          Hidden when the matrix offers no destinations for medications. */}
      <LinksSection
        srcType="medication"
        value={links}
        onChange={(next) => onChange({ links: next })}
        hideWhenEmpty
      />
    </div>
  );
};
