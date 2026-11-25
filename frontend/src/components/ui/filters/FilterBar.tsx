import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { FilterBar as LibraryFilterBar, type FilterFacet, type FilterValue as LibraryFilterValue } from '@neuronection/assistant-ui/filters';
import type { FacetDefinition, FacetOption } from './types';
import { filterLabels, fromLibraryValue, toLibraryFacet, toLibraryValue } from './adapter';
import { defaultFilterValue, type UseFilterStateResult } from './useFilterState';
import { formatNumber } from '../../../lib/dateFormats';

/** Resolve a facet's options: static `options` first, else derive from items. */
export function resolveOptions<T>(facet: FacetDefinition<T>, items: T[]): FacetOption[] {
  if (facet.options) return facet.options;
  if (facet.getOptions) return facet.getOptions(items);
  return [];
}

export interface FilterBarProps<T> {
  facets: FacetDefinition<T>[];
  filter: UseFilterStateResult<T>;
  /** Loaded items — used to derive option lists for `getOptions` facets. */
  items: T[];
  /** Shown in the summary when provided alongside `totalCount`. */
  resultCount?: number;
  totalCount?: number;
  /** Override the default pill visibility for all chips. */
  showActivePills?: boolean;
  className?: string;
}

/**
 * Health's filter bar: the family `FilterBar` driven by `useFilterState`.
 *
 * The bar itself (chips, "More" disclosure, clear-all, summary) is the
 * library's; this adapter resolves facet labels/options from health's model and
 * maps library values back into health's `FilterValue` (nullable single, range).
 */
export const FilterBar = <T,>({
  facets,
  filter,
  items,
  resultCount,
  totalCount,
  showActivePills,
  className = '',
}: FilterBarProps<T>) => {
  const { t } = useTranslation();

  const { libraryFacets, values, byId } = useMemo(() => {
    const facetModels: FilterFacet[] = [];
    const resolvedValues: Record<string, LibraryFilterValue> = {};
    const lookup = new Map<string, FacetDefinition<T>>();

    for (const facet of facets) {
      // `range` facets are not modelled by the library module (they render
      // through RangeBar/ScaleSlider); everything else is.
      const model = toLibraryFacet(facet, t, resolveOptions(facet, items));
      if (!model) continue;
      // A facet with no state entry still renders at its default — the old bar
      // did that immediately, and hiding it for a frame caused a layout shift.
      const libraryValue = toLibraryValue(filter.state[facet.id] ?? defaultFilterValue(facet));
      if (!libraryValue) continue;
      facetModels.push(model);
      resolvedValues[facet.id] = libraryValue;
      lookup.set(facet.id, facet);
    }

    return { libraryFacets: facetModels, values: resolvedValues, byId: lookup };
  }, [facets, items, filter.state, t]);

  const summary =
    resultCount !== undefined && totalCount !== undefined
      ? t('filters.result_count', { shown: resultCount, total: totalCount })
      : undefined;

  return (
    <LibraryFilterBar
      facets={libraryFacets}
      values={values}
      onChange={(facetId, value) => {
        const facet = byId.get(facetId);
        if (!facet) return;
        const current = filter.state[facetId] ?? defaultFilterValue(facet);
        // Multi facets go through `toggle` (an intent applied to the previous
        // state) so clicks inside one burst are not collapsed. The rest are
        // single-shot replacements.
        if (facet.kind === 'multi' && current.kind === 'multi' && value.kind === 'multi') {
          for (const option of value.values.filter((v) => !current.values.includes(v))) {
            filter.toggle(facetId, option);
          }
          for (const option of current.values.filter((v) => !value.values.includes(v))) {
            filter.toggle(facetId, option);
          }
          return;
        }
        filter.set(facetId, fromLibraryValue(value));
      }}
      onClearAll={filter.clearAll}
      activeCount={filter.activeCount}
      summary={summary}
      showActivePills={showActivePills}
      formatCount={formatNumber}
      labels={filterLabels(t)}
      className={className}
    />
  );
};
