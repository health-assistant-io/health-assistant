import React from 'react';
import { useTranslation } from 'react-i18next';
import { Filter } from 'lucide-react';

export interface FilterSummaryProps {
  /** Number of currently-active facets. */
  activeCount: number;
  /** Items remaining after filtering. */
  resultCount?: number;
  /** Total items before filtering. */
  totalCount?: number;
  className?: string;
}

/**
 * Compact "N filters · M of T results" indicator. Optional — `FilterBar`
 * already shows a result count when given `resultCount`/`totalCount`; this
 * component is for places that want a standalone summary (e.g. an empty-state
 * banner, a drawer header, a sticky toolbar corner).
 */
export const FilterSummary: React.FC<FilterSummaryProps> = ({
  activeCount,
  resultCount,
  totalCount,
  className = '',
}) => {
  const { t } = useTranslation();

  const parts: string[] = [];
  if (activeCount > 0) {
    parts.push(
      t('filters.active_count', {
        defaultValue: '{{count}} filter(s)',
        count: activeCount,
      }),
    );
  }
  if (resultCount !== undefined && totalCount !== undefined) {
    parts.push(
      t('filters.result_count', {
        defaultValue: '{{shown}} of {{total}}',
        shown: resultCount,
        total: totalCount,
      }),
    );
  }

  if (parts.length === 0) return null;

  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-bold text-gray-400 dark:text-dark-muted ${className}`}>
      <Filter className="w-3.5 h-3.5" />
      {parts.join(' · ')}
    </span>
  );
};
