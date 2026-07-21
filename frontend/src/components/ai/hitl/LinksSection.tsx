/**
 * LinksSection — schema-driven related-items editor for HITL create forms.
 *
 * Thin wrapper around {@link CatalogItemPicker} (relation mode) that:
 *  - Discovers which destination types + relations are valid for the form's
 *    primary entity via {@link useLinkSchema} (server-side `LINK_SCHEMA` is
 *    the single source of truth — no per-form declaration).
 *  - Restricts the picker's `allowedTypes` to those destinations.
 *  - Filters each chip's relation dropdown to the relations valid for THAT
 *    chip's destination type (via the picker's `getRelationsForType` prop).
 *
 * Used by Phase-3 form integrations: a `catalog/forms/MedicationForm` (or
 * `BiomarkerForm`/etc.) declares only `srcType="medication"` and `<LinksSection>`
 * auto-renders every link destination the matrix allows for medications
 * (concepts the drug TREATS, biomarkers it MONITORS, event types it INDICATES,
 * etc.).
 *
 * Modes:
 *  - `'editable'` (default) — full picker UI (search + browse + chips).
 *  - `'summary'`             — read-only chips only (for the compact HITL card).
 *  - `'readonly'`            — same as 'summary' today; reserved for future
 *                              denser layouts (e.g. a definition table).
 *
 * The form owns the state (`value` / `onChange`). Persistence happens in the
 * form's `onSubmit` via {@link createLinksFor} AFTER the primary create
 * returns the new id — this component never persists on its own.
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link2 } from 'lucide-react';
import { CatalogItemPicker } from '../../catalog/CatalogItemPicker';
import { useLinkSchema } from '../../../hooks/useLinkSchema';
import {
  RELATION_OPTION_GROUPS,
  filterRelationGroups,
  type RelationOptionGroup,
} from '../../catalog/catalogRelationTypes';
import type { CatalogSelection, CatalogType } from '../../../types/catalog';

export type LinksSectionMode = 'editable' | 'summary' | 'readonly';

export interface LinksSectionProps {
  /** The primary entity's polymorphic edge-endpoint type ('medication',
   *  'biomarker', 'allergy', 'vaccine', 'anatomy', 'concept',
   *  'clinical_event_type'). Determines which destinations are offered. */
  srcType: string;
  /** Controlled state. The form owns this; pass `proposed_payload.links` (re-shaped
   *  into `CatalogSelection[]`) as the initial value when integrating the HITL flow. */
  value: CatalogSelection[];
  onChange: (next: CatalogSelection[]) => void;
  mode?: LinksSectionMode;
  /** Optional: override the section title. Defaults to a localized 'Related items'. */
  title?: string;
  /** Optional: hide the section entirely when there are no links AND the
   *  matrix offers no destinations for this srcType. Useful for compact forms. */
  hideWhenEmpty?: boolean;
  /** Optional: extra className on the section wrapper. */
  className?: string;
  /** Optional: placeholder text for the picker input. */
  placeholder?: string;
}

/** Map a catalog-type string back to itself (identity for the 6 catalog types).
 *  CatalogItemPicker uses catalog-type strings as `allowedTypes`, so we pass
 *  the schema's dst_type values straight through. 'immunization' (the
 *  EdgeEndpointType for vaccine) is normalised to 'vaccine' (the CatalogType). */
function dstTypeToCatalogType(dstType: string): CatalogType | undefined {
  if (dstType === 'immunization') return 'vaccine' as CatalogType;
  // The other 5 endpoint types already match their catalog-type strings.
  // Non-catalog endpoint types (observation, doctor, examination, document)
  // don't have a CatalogType and the picker would reject them — the
  // LINK_SCHEMA may still advertise them for advanced flows; we hide those
  // from the picker for now (no instance-picker available for them).
  if (
    dstType === 'biomarker' ||
    dstType === 'medication' ||
    dstType === 'allergy' ||
    dstType === 'anatomy' ||
    dstType === 'concept' ||
    dstType === 'clinical_event_type'
  ) {
    return dstType as CatalogType;
  }
  return undefined;
}

export const LinksSection: React.FC<LinksSectionProps> = ({
  srcType,
  value,
  onChange,
  mode = 'editable',
  title,
  hideWhenEmpty = false,
  className = '',
  placeholder,
}) => {
  const { t } = useTranslation();
  // Always call the hook unconditionally (rules of hooks). When srcType is
  // empty we pass a sentinel that matches no rows so the result is `{}`.
  const { schema, loading, error } = useLinkSchema(srcType);

  /** Allowed destination catalog types (filtered to ones the picker supports). */
  const allowedTypes = useMemo<CatalogType[]>(() => {
    if (!schema) return [];
    return Object.keys(schema)
      .map(dstTypeToCatalogType)
      .filter((t): t is CatalogType => t !== undefined);
  }, [schema]);

  /** Per-catalog-type relation option groups. */
  const getRelationsForType = useMemo(() => {
    return (catalogType: string): RelationOptionGroup[] | undefined => {
      if (!schema) return undefined;
      // schema is keyed by EdgeEndpointType; catalogType may be 'vaccine'
      // (the only mismatch). Normalise before lookup.
      const key = catalogType === 'vaccine' ? 'immunization' : catalogType;
      const allowed = schema[key];
      if (!allowed || allowed.length === 0) return undefined;
      return filterRelationGroups(RELATION_OPTION_GROUPS, allowed);
    };
  }, [schema]);

  // Hide when no destinations exist for this srcType AND user has nothing yet.
  if (
    hideWhenEmpty &&
    value.length === 0 &&
    !loading &&
    allowedTypes.length === 0
  ) {
    return null;
  }

  const isReadOnly = mode === 'summary' || mode === 'readonly';
  const labelText =
    title ?? t('ai_chat.hitl.links.section_title', 'Related items');

  return (
    <div className={`space-y-2 ${className}`}>
      <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-500">
        <Link2 className="w-3.5 h-3.5" />
        {labelText}
      </div>

      {loading ? (
        <p className="text-xs text-gray-400">
          {t('common.loading', 'Loading…')}
        </p>
      ) : error ? (
        <p className="text-xs text-red-500">
          {t('ai_chat.hitl.links.error', 'Could not load link schema.') }
        </p>
      ) : isReadOnly ? (
        value.length === 0 ? (
          <p className="text-xs text-gray-400">
            {t('ai_chat.hitl.links.none', 'No related items.')}
          </p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {value.map((sel, idx) => (
              <li
                key={`${sel.type}:${sel.id}:${sel.relation ?? ''}:${idx}`}
                className="flex items-center gap-1.5 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 pl-2.5 pr-2 py-1 text-xs"
              >
                <span className="text-[10px] font-bold uppercase tracking-wide text-blue-500 shrink-0">
                  {sel.relation ?? ''}
                </span>
                <span className="text-gray-700 dark:text-gray-200 truncate max-w-[14rem]">
                  {sel.label}
                </span>
                <span className="text-[10px] text-gray-400 uppercase">
                  {sel.type}
                </span>
              </li>
            ))}
          </ul>
        )
      ) : allowedTypes.length === 0 ? (
        <p className="text-xs text-gray-400">
          {t('ai_chat.hitl.links.no_destinations', 'No link destinations available.') }
        </p>
      ) : (
        <CatalogItemPicker
          mode="multi"
          value={value}
          onChange={onChange}
          allowedTypes={allowedTypes}
          relationPicker={{}} // enable relation chips; default relations applied
          getRelationsForType={getRelationsForType}
          placeholder={
            placeholder ??
            t('ai_chat.hitl.links.placeholder', 'Search catalog items to link…')
          }
        />
      )}
    </div>
  );
};
