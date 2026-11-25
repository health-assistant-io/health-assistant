import React from 'react';
import type { FilterBarLabels, FilterFacet, FilterOption, FilterValue as LibraryFilterValue } from '@neuronection/assistant-ui/filters';
import { DynamicIcon } from '../DynamicIcon';
import type { FacetDefinition, FacetIcon, FacetOption, FilterValue } from './types';

/** Translation function shape the adapters need (kept structural, not i18next-specific). */
export type Translate = (key: string, options?: Record<string, unknown>) => string;

/** Render a facet icon — DynamicIcon for strings/IconConfigs, raw node otherwise. */
export function renderFacetIcon(icon: FacetIcon | undefined | null, className: string): React.ReactNode {
  if (icon === undefined || icon === null) return null;
  if (React.isValidElement(icon)) return icon;
  return <DynamicIcon icon={icon as string | { type: 'lucide' | 'custom_svg'; value: string }} className={className} />;
}

function toLibraryOption(option: FacetOption): FilterOption {
  return {
    value: option.value,
    label: option.label,
    icon: renderFacetIcon(option.icon, 'size-4 shrink-0'),
    count: option.count,
  };
}

/**
 * Map a health facet onto the library's view model.
 *
 * Returns `null` for `range` facets: the library models `single`/`multi`/
 * `toggle` only, and range filters in health render through their own controls
 * (`RangeBar`/`ScaleSlider`), not through a chip.
 */
export function toLibraryFacet<T>(
  facet: FacetDefinition<T>,
  t: Translate,
  options: FacetOption[] = facet.options ?? [],
): FilterFacet | null {
  if (facet.kind === 'range') return null;
  return {
    id: facet.id,
    label: facet.i18nKey ? t(facet.i18nKey, { defaultValue: facet.label }) : facet.label,
    kind: facet.kind,
    icon: renderFacetIcon(facet.icon, 'size-4'),
    hidden: facet.defaultHidden,
    options: facet.kind === 'toggle' ? undefined : options.map(toLibraryOption),
  };
}

/**
 * Map a health value onto the library's. `single.value` is nullable in health
 * and a plain string in the library, so "no selection" becomes `''`.
 */
export function toLibraryValue(value: FilterValue): LibraryFilterValue | null {
  switch (value.kind) {
    case 'toggle':
      return { kind: 'toggle', on: value.on };
    case 'multi':
      return { kind: 'multi', values: value.values };
    case 'single':
      return { kind: 'single', value: value.value ?? '' };
    default:
      return null;
  }
}

export function fromLibraryValue(value: LibraryFilterValue): FilterValue {
  if (value.kind === 'toggle') return { kind: 'toggle', on: value.on };
  if (value.kind === 'multi') return { kind: 'multi', values: value.values };
  return { kind: 'single', value: value.value === '' ? null : value.value };
}

/** The library's copy, resolved from health's dictionary. */
export function filterLabels(t: Translate): FilterBarLabels {
  return {
    noOptions: t('common.no_options'),
    noMatches: t('filters.no_matches'),
    searchPlaceholder: t('filters.search_options_placeholder'),
    searchAriaLabel: t('filters.search_options'),
    selectedCount: (count) => t('filters.selected_count', { count }),
    removeNamed: (label) => t('common.remove_named', { name: label }),
    optionWithCount: (label, count) => t('filters.option_with_count', { label, count }),
    more: t('filters.more'),
    fewer: t('filters.fewer'),
    clearAll: t('filters.clear_all'),
  };
}
