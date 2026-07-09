/**
 * Biomarker create/edit form — slug, code, aliases, reference range, preferred
 * unit, info. Fields mirror the backend `BiomarkerCreate` schema.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput, TextArea } from './FormFields';
import biomarkerService from '../../../services/biomarkerService';
import type { Unit } from '../../../types/biomarker';

export const BiomarkerForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
}) => {
  const { t } = useTranslation();
  const [units, setUnits] = useState<Unit[]>([]);

  useEffect(() => {
    biomarkerService.getUnits().then(setUnits).catch(() => {});
  }, []);

  const aliases = Array.isArray(values.aliases) ? (values.aliases as string[]) : [];
  const codingSystem = String(values.coding_system ?? 'custom');

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
            placeholder="e.g. fasting-glucose"
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Coding system">
          <select
            value={codingSystem}
            onChange={(e) => onChange({ coding_system: e.target.value })}
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
          >
            <option value="loinc">LOINC</option>
            <option value="snomed">SNOMED</option>
            <option value="custom">Custom</option>
          </select>
        </Field>
        <Field label="Code">
          <TextInput
            value={String(values.code ?? '')}
            onChange={(e) => onChange({ code: e.target.value })}
            placeholder="e.g. 2345-7"
          />
        </Field>
      </div>
      <Field label="Aliases" hint={t('catalogs.field_aliases_hint', 'Comma-separated')}>
        <TextInput
          value={aliases.join(', ')}
          onChange={(e) =>
            onChange({
              aliases: e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="FBS, Glucose"
        />
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Ref. min">
          <TextInput
            type="number"
            value={String(values.reference_range_min ?? '')}
            onChange={(e) =>
              onChange({
                reference_range_min:
                  e.target.value === '' ? null : Number(e.target.value),
              })
            }
          />
        </Field>
        <Field label="Ref. max">
          <TextInput
            type="number"
            value={String(values.reference_range_max ?? '')}
            onChange={(e) =>
              onChange({
                reference_range_max:
                  e.target.value === '' ? null : Number(e.target.value),
              })
            }
          />
        </Field>
        <Field label="Preferred unit">
          <select
            value={String(values.preferred_unit_id ?? '')}
            onChange={(e) => onChange({ preferred_unit_id: e.target.value || null })}
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
          >
            <option value="">—</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                {u.symbol} ({u.name})
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Info / notes">
        <TextArea
          value={String(values.info ?? '')}
          onChange={(e) => onChange({ info: e.target.value })}
          rows={2}
        />
      </Field>
    </div>
  );
};
