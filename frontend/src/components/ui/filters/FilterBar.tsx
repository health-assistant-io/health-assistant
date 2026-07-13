import React, { useMemo, useState } from 'react';
import { X, MoreHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { FacetDefinition, FacetOption } from './types';
import { FacetChip } from './FacetChip';
import type { UseFilterStateResult } from './useFilterState';

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
  const [showMore, setShowMore] = useState(false);

  const { visible, hidden } = useMemo(
    () => {
      const vis: FacetDefinition<T>[] = [];
      const hid: FacetDefinition<T>[] = [];
      for (const f of facets) (f.defaultHidden ? hid : vis).push(f);
      return { visible: vis, hidden: hid };
    },
    [facets],
  );

  const renderChip = (facet: FacetDefinition<T>) => (
    <FacetChip<T>
      key={facet.id}
      facet={facet}
      value={filter.state[facet.id]}
      options={resolveOptions(facet, items)}
      onValueChange={(v) => filter.set(facet.id, v)}
      onToggleOption={(opt) => filter.toggle(facet.id, opt)}
      showActivePills={showActivePills}
    />
  );

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      {visible.map(renderChip)}

      {hidden.length > 0 && (
        <>
          {showMore && hidden.map(renderChip)}
          <button
            type="button"
            onClick={() => setShowMore((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface text-sm font-bold text-gray-600 dark:text-dark-muted hover:border-blue-200 transition-all whitespace-nowrap"
          >
            <MoreHorizontal className="w-4 h-4" />
            <span>{showMore ? t('filters.fewer', { defaultValue: 'Fewer' }) : t('filters.more', { defaultValue: 'More' })}</span>
          </button>
        </>
      )}

      {filter.isActive && (
        <button
          type="button"
          onClick={filter.clearAll}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-sm font-bold text-gray-500 dark:text-dark-muted hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-all whitespace-nowrap"
        >
          <X className="w-4 h-4" />
          <span>{t('filters.clear_all', { defaultValue: 'Clear all' })}</span>
        </button>
      )}

      {resultCount !== undefined && totalCount !== undefined && (
        <span className="ml-auto text-xs font-bold text-gray-400 dark:text-dark-muted whitespace-nowrap">
          {t('filters.result_count', {
            defaultValue: '{{shown}} of {{total}}',
            shown: resultCount,
            total: totalCount,
          })}
        </span>
      )}
    </div>
  );
};
