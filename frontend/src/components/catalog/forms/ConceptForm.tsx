/**
 * Concept create/edit form — multi-kind, parent, coding, icon, aliases, status.
 *
 * Registered in ``CATALOG_FORMS`` so the catalog modal's ``getCatalogForm``
 * picks it up for ``type='concept'``. Writes route through ``/concepts`` (not
 * ``/catalogs/concept``) via the write-target dispatch (plan §4.3).
 *
 * Fields mirror ``ConceptCreateInput`` / ``ConceptUpdateInput``:
 * slug (create-only), name, kinds, parent, coding system + code, aliases,
 * icon, color, display_order, status (edit-only).
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItemFormProps } from './catalogForms';
import { Field, TextInput } from './FormFields';
import { RichTextField } from './RichTextField';
import { KindChips } from './KindChips';
import { IconPicker } from '../../ui/IconPicker';
import { CatalogItemPicker } from '../CatalogItemPicker';
import { LinksSection } from '../../ai/hitl/LinksSection';
import type { CatalogSelection } from '../../../types/catalog';
import type { ConceptKind, IconConfig } from '../../../types/concept';

const CODING_SYSTEMS = [
  'loinc',
  'snomed',
  'atc',
  'icd10',
  'cvx',
  'mesh',
  'fma',
  'custom',
];

export const ConceptForm: React.FC<CatalogItemFormProps> = ({
  values,
  onChange,
  mode,
}) => {
  const { t } = useTranslation();
  const kinds = (values.kinds as ConceptKind[]) ?? [];
  const aliases = Array.isArray(values.aliases) ? (values.aliases as string[]) : [];
  const icon = (values.icon as IconConfig | null | undefined) ?? {
    type: 'lucide',
    value: 'Network',
  };

  // Parent concept picker value
  const parentValue =
    values.parent_id != null
      ? [{
          type: 'concept' as const,
          id: String(values.parent_id),
          label: (values as any).parent_slug
            ? String((values as any).parent_slug)
            : String(values.parent_id),
        }]
      : [];

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_name', 'Name')}>
          <TextInput
            value={String(values.name ?? '')}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="e.g. Cardiology"
          />
        </Field>
        {mode === 'create' ? (
          <Field
            label={t('catalogs.field_slug', 'Slug')}
            hint={t('catalogs.field_slug_hint', 'kebab-case, immutable after creation')}
          >
            <TextInput
              value={String(values.slug ?? '')}
              onChange={(e) => onChange({ slug: e.target.value })}
              placeholder="auto-derived from name"
            />
          </Field>
        ) : (
          <Field label={t('catalogs.field_slug', 'Slug')}>
            <TextInput value={String(values.slug ?? '')} disabled />
          </Field>
        )}
      </div>

      <Field
        label={t('catalogs.field_kinds', 'Kinds (domains)')}
        hint={t('catalogs.field_kinds_hint', 'Which catalog domains this concept belongs to')}
      >
        <KindChips
          value={kinds}
          onChange={(next) => onChange({ kinds: next })}
        />
      </Field>

      <Field label={t('catalogs.field_parent', 'Parent concept')}>
        <CatalogItemPicker
          mode="single"
          allowedTypes={['concept']}
          value={parentValue}
          onChange={(next) =>
            onChange({
              parent_id: next.length > 0 ? next[0].id : null,
            })
          }
          placeholder={t('catalogs.field_parent_placeholder', 'Search for a parent concept…')}
          block
        />
      </Field>

      <RichTextField
        label={t('catalogs.field_description', 'Description')}
        value={String(values.description ?? '')}
        onChange={(html) => onChange({ description: html })}
      />

      <div className="grid grid-cols-2 gap-3">
        <Field label={t('catalogs.field_coding_system', 'Coding system')}>
          <select
            value={String(values.coding_system ?? '')}
            onChange={(e) => onChange({ coding_system: e.target.value || null })}
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none"
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
            value={String(values.code ?? '')}
            onChange={(e) => onChange({ code: e.target.value || null })}
            placeholder="e.g. 394579002"
          />
        </Field>
      </div>

      <Field
        label={t('catalogs.field_aliases', 'Aliases')}
        hint={t('catalogs.field_aliases_hint', 'Comma-separated')}
      >
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
          placeholder="cardiac, heart"
        />
      </Field>

      <div className="flex items-start gap-4">
        <Field label={t('catalogs.field_icon', 'Icon')}>
          <IconPicker
            value={icon}
            onChange={(next) => onChange({ icon: next })}
            color={(values.color as string) ?? undefined}
          />
        </Field>
        <Field label={t('catalogs.field_color', 'Color')}>
          <input
            type="color"
            value={String(values.color ?? '#3b82f6')}
            onChange={(e) => onChange({ color: e.target.value })}
            className="h-10 w-16 rounded-lg border border-gray-300 dark:border-gray-600 cursor-pointer"
          />
        </Field>
        <Field label={t('catalogs.field_order', 'Display order')}>
          <TextInput
            type="number"
            value={String(values.display_order ?? 0)}
            onChange={(e) =>
              onChange({ display_order: parseInt(e.target.value, 10) || 0 })
            }
            className="w-20"
          />
        </Field>
      </div>

      {mode === 'edit' && (
        <Field label={t('catalogs.field_status', 'Status')}>
          <select
            value={String(values.status ?? 'active')}
            onChange={(e) => onChange({ status: e.target.value })}
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none"
          >
            <option value="active">Active</option>
            <option value="draft">Draft</option>
            <option value="retired">Retired</option>
          </select>
        </Field>
      )}

      {/* Concept→concept semantic relations (MEMBER_OF, TREATS, AFFECTS, etc.). */}
      <LinksSection
        srcType="concept"
        value={Array.isArray(values.links) ? (values.links as CatalogSelection[]) : []}
        onChange={(next) => onChange({ links: next })}
        hideWhenEmpty
      />
    </div>
  );
};
