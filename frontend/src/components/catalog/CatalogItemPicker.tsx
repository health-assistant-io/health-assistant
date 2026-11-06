/**
 * CatalogItemPicker — reusable search-and-select for catalog items.
 *
 * A thin adapter over the shared `multi-select-picker` module
 * (`components/ui/multiSelectPicker`) — the family's ONE implementation of
 * the "type-ahead + browse modal" pattern. The adapter maps
 * `CatalogSelection` ↔ `PickerSelection` (`catalogPickerAdapter`) and owns
 * the catalog data closures; the module owns the input + type-ahead (the
 * debounced {@link searchCatalogs} call becomes its `onSearch`), the chips
 * (per-chip relation dropdown via `renderChipExtra`) and the browse modal
 * (`CatalogPickerBrowseModal` supplies its catalog-domain chrome through
 * the module's slots).
 *
 * Selection model:
 *   - `mode: 'single'` → picking replaces the selection (length ≤ 1).
 *   - `mode: 'multi'`  → appends; deduped by `type:id` (+ `relation` when used
 *     in relation mode).
 *
 * Relation binding: pass `relationPicker` to surface a relation-type dropdown
 * on each selected chip (output entries carry `relation`). Mirrors the
 * `CatalogRelationsEditor` pattern, which now delegates to this component.
 *
 * `displayMode: 'cards'` stays app-side: the rich {@link CatalogItemCard}s
 * render below the picker from the controlled value (`showChips` off).
 *
 * Controlled: `value: CatalogSelection[]` + `onChange`.
 */
import React, { useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  MultiSelectPicker,
  type PickerOption,
  type PickerSelection,
} from '../ui/multiSelectPicker';
import { RelationTypeSelect } from './RelationTypeSelect';
import { useCatalogPickerBrowse } from './CatalogPickerBrowseModal';
import { CatalogItemCard } from './CatalogItemCard';
import { searchCatalogs } from '../../services/catalogService';
import {
  DEFAULT_RELATION,
  RELATION_OPTION_GROUPS,
  type RelationOptionGroup,
} from './catalogRelationTypes';
import {
  fromPickerSelection,
  hitToOption,
  selectionKey,
  toPickerSelection,
} from './catalogPickerAdapter';
import type {
  CatalogSelection,
  CatalogType,
} from '../../types/catalog';

export interface CatalogItemPickerProps {
  /** Always an array; in `single` mode length is 0 or 1. */
  value: CatalogSelection[];
  onChange: (next: CatalogSelection[]) => void;
  mode?: 'single' | 'multi';
  /** Restrict to a subset of catalog types; default = all registered. */
  allowedTypes?: CatalogType[];
  /** ConceptKind value (e.g. `'event_category'`, `'specialty'`) that narrows
   *  the `concept` catalog. Only meaningful when `allowedTypes` includes
   *  `'concept'`; ignored for other catalogs. Lets the same picker power
   *  examination/event-category + specialty selection. */
  conceptKind?: string;
  /** Enable relation binding (chips get a relation dropdown). */
  relationPicker?: {
    /** Override the default option groups (mirrors ConceptRelationType). */
    options?: RelationOptionGroup[];
    /** Relation applied to new picks; default 'AFFECTS'. */
    defaultRelation?: string;
  };
  /** Optional: per-catalog-type relation filtering. When provided, each chip's
   *  relation dropdown shows only the relations valid for THAT chip's type
   *  (e.g. medication→concept shows TREATS/CONTRAINDICATES, medication→biomarker
   *  shows MONITORS/CORRELATES_WITH). Falls back to `relationPicker.options`
   *  / the global default when not provided or when the callback returns
   *  undefined. Powers the schema-driven `<LinksSection>` for HITL link
   *  proposals; existing callers are unchanged. */
  getRelationsForType?: (
    catalogType: string,
  ) => RelationOptionGroup[] | undefined;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  /** Kept for API compatibility (form rows pass it); the shared picker input
   *  is always full-width. */
  block?: boolean;
  /**
   * How selected items render below the input:
   *   - `'chips'` (default) — compact label chips (the original behavior).
   *   - `'cards'` — rich {@link CatalogItemCard} per item (fetches each
   *     definition via `/catalogs/{type}/{id}` so the type-specific fields show:
   *     a medication surfaces indications / dosage / side effects, etc.). Use
   *     when a picked catalog definition should be scannable at a glance instead
   *     of a bare chip. Mirrors `InstancePicker.displayMode`.
   */
  displayMode?: 'chips' | 'cards';
  /**
   * Cards-mode only: render extra content inside each selected card's footer
   * (e.g. a per-link control). Receives the selection + its index so the caller
   * can bind to its own per-link state. The picker owns selection (add/remove);
   * the caller owns the per-link extras. Mirrors `InstancePicker`.
   */
  renderCardFooter?: (
    selection: CatalogSelection,
    idx: number,
  ) => React.ReactNode;
}

export { selectionKey };

export const CatalogItemPicker: React.FC<CatalogItemPickerProps> = ({
  value,
  onChange,
  mode = 'multi',
  allowedTypes,
  conceptKind,
  relationPicker,
  getRelationsForType,
  placeholder,
  className = '',
  disabled = false,
  block = false,
  displayMode = 'chips',
  renderCardFooter,
}) => {
  const { t } = useTranslation();

  const allowedList = useMemo(
    () => (allowedTypes ? allowedTypes.map(String) : undefined),
    [allowedTypes],
  );
  const relationGroups = relationPicker?.options ?? RELATION_OPTION_GROUPS;
  const defaultRelation = relationPicker?.defaultRelation ?? DEFAULT_RELATION;
  const relationDefault = relationPicker ? defaultRelation : undefined;

  const typeAheadSearch = useCallback(
    async (query: string): Promise<PickerOption[]> => {
      try {
        // Push type + kind filtering server-side: the backend applies the
        // per-type-floor guarantee to the allowed types and ignores kind for
        // non-concept catalogs.
        const resp = await searchCatalogs(query, {
          limit: 10,
          types: allowedList?.length ? allowedList.join(',') : undefined,
          kind: conceptKind,
        });
        return resp.results.map((hit) => hitToOption(hit, relationDefault));
      } catch {
        return [];
      }
    },
    [allowedList, conceptKind, relationDefault],
  );

  const { browseModalProps, onBrowseOpenChange } = useCatalogPickerBrowse({
    allowedList,
    conceptKind,
    relationDefault,
  });

  const pickerValue = useMemo(
    () => value.map((sel) => toPickerSelection(sel, relationDefault)),
    [value, relationDefault],
  );

  const handleChange = useCallback(
    (next: PickerSelection[]) => onChange(next.map(fromPickerSelection)),
    [onChange],
  );

  const removeAt = useCallback(
    (idx: number) => {
      onChange(value.filter((_, i) => i !== idx));
    },
    [value, onChange],
  );

  const setRelationAt = useCallback(
    (idx: number, relation: string) => {
      onChange(value.map((s, i) => (i === idx ? { ...s, relation } : s)));
    },
    [value, onChange],
  );

  /** Chip prefix: the relation dropdown in relation mode, the catalog-type
   *  badge otherwise. */
  const renderChipExtra = useCallback(
    (selection: PickerSelection, idx: number): React.ReactNode => {
      const sel = fromPickerSelection(selection);
      if (!relationPicker) {
        return (
          <span className="shrink-0 text-[10px] font-bold uppercase tracking-wide text-blue-500">
            {sel.type}
          </span>
        );
      }
      const chipGroups = getRelationsForType?.(sel.type) ?? relationGroups;
      return (
        <RelationTypeSelect
          value={sel.relation ?? defaultRelation}
          onChange={(r) => setRelationAt(idx, r)}
          options={chipGroups}
        />
      );
    },
    [relationPicker, relationGroups, defaultRelation, getRelationsForType, setRelationAt],
  );

  const placeholderText =
    placeholder ?? t('catalogs.picker_placeholder', 'Search any catalog…');

  const labels = useMemo(
    () => ({
      placeholder: placeholderText,
      inputLabel: placeholderText,
      searchPlaceholder: t('catalogs.picker_search', 'Search items…'),
      browseLabel: t('catalogs.picker_browse', 'Browse'),
      browseTitle: t('catalogs.picker_browse', 'Browse catalog'),
      removeLabel: t('common.remove', 'Remove'),
      addLabel: t('common.add', 'Add'),
      addedLabel: t('catalogs.picker_added', 'Added'),
      loadMoreLabel: t('catalogs.load_more', 'Load more'),
      doneLabel: t('common.done', 'Done'),
      closeLabel: t('common.close', 'Close'),
      emptyLabel: t('catalogs.edge_no_results', 'No matches.'),
      searchingLabel: t('common.searching', 'Searching…'),
      categoryLabel: t('catalogs.type_select_title', 'Catalog type'),
      allCategoriesLabel: t('catalogs.picker_type_all', 'All catalogs'),
      selectedCountLabel: (count: number) =>
        count === 0
          ? t('catalogs.picker_none_selected', 'No items selected')
          : t('catalogs.picker_n_selected', {
              count,
              defaultValue: '{{count}} selected',
            }),
    }),
    [t, placeholderText],
  );

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      <MultiSelectPicker
        value={pickerValue}
        onChange={handleChange}
        mode={mode}
        onSearch={typeAheadSearch}
        minQueryLength={2}
        maxResults={10}
        disabled={disabled}
        placeholder={placeholderText}
        labels={labels}
        showChips={displayMode !== 'cards'}
        renderChipExtra={renderChipExtra}
        browseModalProps={browseModalProps}
        onBrowseOpenChange={onBrowseOpenChange}
        className={block ? 'w-full' : undefined}
      />

      {value.length > 0 && displayMode === 'cards' && (
        <div className="flex flex-col gap-2">
          {value.map((sel, idx) => {
            const chipGroups =
              getRelationsForType?.(sel.type) ?? relationGroups;
            return (
              <CatalogItemCard
                key={`${sel.type}:${sel.id}:${sel.relation ?? ''}:${idx}`}
                selection={sel}
                onRemove={() => removeAt(idx)}
                footer={renderCardFooter?.(sel, idx)}
                actions={
                  relationPicker ? (
                    <RelationTypeSelect
                      value={sel.relation ?? defaultRelation}
                      onChange={(r) => setRelationAt(idx, r)}
                      options={chipGroups}
                    />
                  ) : undefined
                }
              />
            );
          })}
        </div>
      )}
    </div>
  );
};
