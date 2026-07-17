/**
 * Instance facet registry — the single source of truth for patient-record
 * (instance) list filters. Each entity ships one `get<Entity>Facets(ctx)`
 * module consumed by BOTH its instance adapter (→ the InstanceBrowseModal /
 * InstancePicker) AND its main listing/analytics page, so a filter is defined
 * once and behaves identically everywhere.
 *
 * Context (`ctx`): category facets accept pre-fetched options (e.g. exam/event
 * categories from the concepts endpoint) so listing pages can show the full
 * category set including zero-match ones. Without `ctx`, options are derived
 * from the loaded items (browse-modal client mode). Same definition, two option
 * strategies.
 *
 * Catalog (definition) facets live separately in `catalogFacetRegistry` —
 * catalog rows are a different domain (is_custom, coding_system, …) from
 * patient-record rows (status, interpretation).
 */
import type {
  FacetDefinition,
  FacetOption,
} from '../../../components/ui/filters/types';
import { deriveCountedOptions } from '../../../components/ui/filters/deriveCountedOptions';

export interface CategoryFacetCtx {
  /** Pre-fetched category options. When supplied, the facet uses them as static
   *  options (listing pages show the full set incl. zero-match). Otherwise
   *  options are derived from the loaded items. */
  categoryOptions?: FacetOption[];
}

/** Multi-select facet over a string field; options derived (counted) from items. */
export function multiFacet<T>(
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
    getOptions: (items: T[]) => deriveCountedOptions(items, extract, opts?.labelFn),
    predicate: (item, value) =>
      value.kind === 'multi' && value.values.includes(extract(item) ?? ''),
  };
}

/** Category facet — multi-select with either injected options or derived ones. */
export function categoryFacet<T>(
  id: string,
  label: string,
  extract: (item: T) => string | null | undefined,
  ctx: CategoryFacetCtx | undefined,
  opts?: { icon?: string; labelFn?: (value: string) => string },
): FacetDefinition<T> {
  return {
    id,
    label,
    kind: 'multi',
    mode: 'client',
    icon: opts?.icon ?? 'Tag',
    ...(ctx?.categoryOptions
      ? { options: ctx.categoryOptions }
      : { getOptions: (items: T[]) => deriveCountedOptions(items, extract, opts?.labelFn) }),
    predicate: (item, value) =>
      value.kind === 'multi' && value.values.includes(extract(item) ?? ''),
  };
}

/** Boolean toggle facet (e.g. "telemetry only", "hide unmapped"). */
export function toggleFacet<T>(
  id: string,
  label: string,
  matches: (item: T) => boolean,
  opts?: { icon?: string },
): FacetDefinition<T> {
  return {
    id,
    label,
    kind: 'toggle',
    mode: 'client',
    icon: opts?.icon,
    predicate: (item, value) => {
      if (value.kind !== 'toggle' || !value.on) return true;
      return matches(item);
    },
  };
}
