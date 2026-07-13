/**
 * Generic, domain-agnostic filter primitives.
 *
 * A "facet" is a single filterable dimension (e.g. category, status, unit).
 * It is declared as plain data (`FacetDefinition<T>`) so it can be reused
 * across views and unit-tested without rendering any UI.
 *
 * Layer 1 of the modular filter system — see
 * `dev/plans/modular-filter-system-2026-07-14.md`.
 */
import type { ReactNode } from 'react';

export type FilterMode = 'client' | 'server';

/**
 * A filter value — discriminated by `kind`. The `kind` matches the facet's
 * `kind`, so consumers can narrow with `value.kind === 'multi'` etc.
 */
export type FilterValue =
  | { kind: 'single'; value: string | null }
  | { kind: 'multi'; values: string[] }
  | { kind: 'toggle'; on: boolean }
  | { kind: 'range'; min: number | null; max: number | null };

/** The full filter state: facet id -> value. */
export type FilterState = Record<string, FilterValue>;

/**
 * Icon spec accepted by facets. Accepts a lucide icon name (string), a
 * structured `IconConfig` (forwarded to `DynamicIcon`), or a raw React node
 * (for callers that pre-render an icon component, e.g. `getEventIcon`).
 */
export type FacetIcon = string | { type: 'lucide' | 'custom_svg'; value: string } | ReactNode;

export interface FacetOption {
  value: string;
  label: string;
  icon?: FacetIcon;
  color?: string | null;
  /** Shown next to the label if present (e.g. item count). */
  count?: number;
}

/**
 * Declarative definition of a single filter facet. Generic over the item type
 * so predicates and option-derivers are fully type-safe.
 *
 * - `mode: 'client'` — filtering runs in the browser via `predicate`.
 * - `mode: 'server'` — the facet is serialized to a backend query param via
 *   `serverParam` + `serverValueSerializer`; the view triggers a refetch.
 *
 * Options can be provided three ways (checked in order at render time):
 *   1. `options` — static or server-fetched.
 *   2. `getOptions(items)` — derived from the currently-loaded items.
 *   3. none — the chip still renders (e.g. a toggle needs no option list).
 */
export interface FacetDefinition<T> {
  /** Stable id — used as the FilterState key and default URL key. */
  id: string;
  label: string;
  kind: 'single' | 'multi' | 'toggle' | 'range';
  mode: FilterMode;

  /** Derive options from loaded items (client-side facets). */
  getOptions?: (items: T[]) => FacetOption[];
  /** Static or pre-fetched options. */
  options?: FacetOption[];

  /** Client-mode: returns true if the item passes this facet. */
  predicate?: (item: T, value: FilterValue) => boolean;

  /** Server-mode: backend query-param name. */
  serverParam?: string;
  /** Server-mode: serialize the value for the API call. Return `undefined` to omit. */
  serverValueSerializer?: (value: FilterValue) => string | string[] | undefined;

  /** When true, the view syncs this facet to the URL (key = `urlKey ?? id`). */
  syncToUrl?: boolean;
  urlKey?: string;

  /** UI hints. */
  icon?: FacetIcon;
  /** Collapse behind a "More filters" toggle in FilterBar. */
  defaultHidden?: boolean;
  i18nKey?: string;
}

/** A URL params record — directly mappable to `URLSearchParams` / `useSearchParams`. */
export type UrlParams = Record<string, string>;
