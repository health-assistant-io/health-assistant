import type { FacetOption } from './types';

/**
 * Derive facet options from items by extracting a string key, counting
 * occurrences, and sorting by count (desc) then label (asc). Null /
 * undefined / empty values are excluded so they never appear as a pickable
 * option.
 *
 * Generic utility — usable by any domain (biomarkers, medications, …).
 */
export function deriveCountedOptions<T>(
  items: T[],
  extract: (item: T) => string | null | undefined,
  labelFn?: (value: string) => string,
): FacetOption[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    const raw = extract(item);
    if (raw === null || raw === undefined || raw === '') continue;
    counts.set(raw, (counts.get(raw) ?? 0) + 1);
  }
  const options: FacetOption[] = [...counts.entries()].map(([value, count]) => ({
    value,
    label: labelFn ? labelFn(value) : value,
    count,
  }));
  options.sort((a, b) => (b.count ?? 0) - (a.count ?? 0) || a.label.localeCompare(b.label));
  return options;
}
