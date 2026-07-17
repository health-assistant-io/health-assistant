/**
 * Shared helpers for instance adapters — kept here so the seven entity adapter
 * modules stay concise and the fetch/filter/facet logic is consistent.
 */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import { deriveCountedOptions } from '../../../components/ui/filters/deriveCountedOptions';
import type { InstanceFetchResult, InstanceQuery } from '../../../components/instances/types';

// Re-export so adapters can import `codeableText` from _shared alongside the
// other helpers. The canonical home is `utils/textFormat` (low-level, no
// feature-layer deps).
export { codeableText } from '../../../utils/textFormat';

/**
 * Case-insensitive substring filter over one or more text fields per item.
 * Returns the items unchanged when ``q`` is absent/empty (no-op).
 */
export function clientFilter<T>(
  items: T[],
  q: string | undefined,
  fields: (item: T) => (string | null | undefined)[],
): T[] {
  const term = q?.trim().toLowerCase();
  if (!term) return items;
  return items.filter((item) =>
    fields(item).some((f) => !!f && f.toLowerCase().includes(term)),
  );
}

/**
 * Normalize a raw service return (which may be an unfiltered/unpaginated
 * array) into an {@link InstanceFetchResult} by applying the client-side ``q``
 * filter. ``total`` becomes the filtered length so the browse modal hides
 * "Load more" (graceful degradation — see plan §Phase 3 normalization note).
 */
export function normalizeResult<T>(
  items: T[],
  query: InstanceQuery,
  fields: (item: T) => (string | null | undefined)[],
): InstanceFetchResult<T> {
  const filtered = clientFilter(items, query.q, fields);
  return { items: filtered, total: filtered.length };
}

/**
 * Build a client-mode multi-select facet over a string field. Options are
 * derived (with counts) from the loaded items; the predicate filters by
 * membership. Reuses {@link deriveCountedOptions} so every adapter's facets
 * share one counting/sorting implementation.
 */
export function stringFacet<T>(
  id: string,
  label: string,
  extract: (item: T) => string | null | undefined,
  opts?: { icon?: string; labelFn?: (value: string) => string },
): FacetDefinition<T> {
  return {
    id,
    label,
    kind: 'multi',
    mode: 'client',
    icon: opts?.icon,
    getOptions: (items) => deriveCountedOptions(items, extract, opts?.labelFn),
    predicate: (item, value) =>
      value.kind === 'multi' && value.values.includes(extract(item) ?? ''),
  };
}
