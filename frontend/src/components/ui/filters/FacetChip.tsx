import React from 'react';
import { useTranslation } from 'react-i18next';
import { FacetChip as LibraryFacetChip } from '@neuronection/assistant-ui/filters';
import type { FacetDefinition, FacetOption, FilterValue } from './types';
import { filterLabels, renderFacetIcon, toLibraryFacet, toLibraryValue } from './adapter';
import { defaultFilterValue } from './useFilterState';
import { formatNumber } from '../../../lib/dateFormats';

export interface FacetChipProps<T> {
  facet: FacetDefinition<T>;
  /** May be briefly undefined while the facets array and the hook's sync effect disagree. */
  value?: FilterValue;
  options: FacetOption[];
  /** Full value replacement (used by toggle-kind chips). */
  onValueChange: (value: FilterValue) => void;
  /** Toggle a single option value (used by single/multi chips). */
  onToggleOption: (optionValue: string) => void;
  /** Show the removable active-value pills row (desktop). Defaults to true. */
  showActivePills?: boolean;
}

/**
 * Health's facet chip: the family `FacetChip` with health's facet model
 * (i18n keys, dynamic icons, nullable single values) mapped onto it.
 *
 * The library exposes exactly one event seam; this adapter keeps both of
 * health's callbacks by forwarding the intent form and routing toggle facets
 * to `onValueChange` and option clicks to `onToggleOption`, which is what the
 * previous local implementation did.
 */
export const FacetChip = <T,>({
  facet,
  value,
  options,
  onValueChange,
  onToggleOption,
  showActivePills = true,
}: FacetChipProps<T>) => {
  const { t } = useTranslation();
  const libraryFacet = toLibraryFacet(facet, t, options);
  // The old chip resolved a missing value to the facet's default; the library
  // requires a value, so keep that behaviour at the boundary.
  const libraryValue = toLibraryValue(value ?? defaultFilterValue(facet));
  if (!libraryFacet || !libraryValue) return null;

  return (
    <LibraryFacetChip
      facet={{
        ...libraryFacet,
        icon: renderFacetIcon(facet.icon, 'size-4'),
      }}
      value={libraryValue}
      options={options.map((option) => ({
        value: option.value,
        label: option.label,
        icon: renderFacetIcon(option.icon, 'size-4 shrink-0'),
        count: option.count,
      }))}
      showActivePills={showActivePills}
      formatCount={formatNumber}
      labels={filterLabels(t)}
      onToggleOption={(optionValue, nextPressed) => {
        if (facet.kind === 'toggle') {
          onValueChange({ kind: 'toggle', on: nextPressed });
          return;
        }
        onToggleOption(optionValue);
      }}
    />
  );
};
