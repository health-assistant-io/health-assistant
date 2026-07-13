import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FacetDefinition, FilterState, FilterValue, UrlParams } from './types';

/**
 * The default (inactive) value for a facet — used as the baseline for
 * `isActive` / `activeCount` detection and for `clear`.
 *
 * Generic in `T` because `FacetDefinition<T>` is invariant in `T`
 * (`T` appears in both the `predicate` param and `getOptions` return).
 */
export function defaultFilterValue<T>(facet: FacetDefinition<T>): FilterValue {
  switch (facet.kind) {
    case 'single':
      return { kind: 'single', value: null };
    case 'multi':
      return { kind: 'multi', values: [] };
    case 'toggle':
      return { kind: 'toggle', on: false };
    case 'range':
      return { kind: 'range', min: null, max: null };
  }
}

/** True when the facet's value equals its default (i.e. not contributing to filtering). */
export function isDefaultValue<T>(facet: FacetDefinition<T>, value: FilterValue): boolean {
  switch (facet.kind) {
    case 'single':
      return value.kind === 'single' && value.value === null;
    case 'multi':
      return value.kind === 'multi' && value.values.length === 0;
    case 'toggle':
      return value.kind === 'toggle' && !value.on;
    case 'range':
      return value.kind === 'range' && value.min === null && value.max === null;
  }
  return false;
}

function buildInitialState<T>(
  facets: FacetDefinition<T>[],
  overrides?: Partial<FilterState>,
): FilterState {
  const state: FilterState = {};
  for (const f of facets) state[f.id] = defaultFilterValue(f);
  if (overrides) {
    for (const [id, value] of Object.entries(overrides)) {
      if (value) state[id] = value;
    }
  }
  return state;
}

function serializeValue(value: FilterValue): string {
  switch (value.kind) {
    case 'single':
      return value.value ?? '';
    case 'multi':
      return value.values.join(',');
    case 'toggle':
      return value.on ? '1' : '0';
    case 'range': {
      const min = value.min !== null ? String(value.min) : '';
      const max = value.max !== null ? String(value.max) : '';
      return `${min}-${max}`;
    }
  }
}

function parseValue(kind: FacetDefinition<unknown>['kind'], raw: string): FilterValue | null {
  switch (kind) {
    case 'single':
      return raw === '' ? null : { kind: 'single', value: raw };
    case 'multi':
      return { kind: 'multi', values: raw === '' ? [] : raw.split(',') };
    case 'toggle':
      return { kind: 'toggle', on: raw === '1' };
    case 'range': {
      const dash = raw.indexOf('-');
      if (dash === -1) return null;
      const minStr = raw.slice(0, dash);
      const maxStr = raw.slice(dash + 1);
      return {
        kind: 'range',
        min: minStr === '' ? null : Number(minStr),
        max: maxStr === '' ? null : Number(maxStr),
      };
    }
  }
}

export interface UseFilterStateResult<T> {
  state: FilterState;
  /** Replace a facet's value entirely. */
  set: (id: string, value: FilterValue) => void;
  /** Toggle an option: add/remove for multi, set/clear for single. */
  toggle: (id: string, optionValue: string) => void;
  /** Reset one facet to its default. */
  clear: (id: string) => void;
  /** Reset every facet to its default. */
  clearAll: () => void;
  /** True when at least one facet has a non-default value. */
  isActive: boolean;
  /** Number of facets with a non-default value. */
  activeCount: number;
  /** Run all active client-mode predicates over `items` (AND semantics). */
  applyFilters: (items: T[]) => T[];
  /** True if a single item passes all active client-mode facets (AND). */
  matches: (item: T) => boolean;
  /** Serialized server-mode params, ready to merge into an API call. */
  serverParams: Record<string, string>;
  /** Emit URL params for facets with `syncToUrl` (only non-default). */
  serialize: () => UrlParams;
  /** Parse URL params back into a FilterState partial (for `initialState`). */
  parse: (params: UrlParams) => FilterState;
}

/**
 * Serialize ALL non-default facet values into a compact JSON string
 * (`{"facetId":"value",...}`). Unlike `serialize()` (which only includes
 * `syncToUrl` facets), this captures every active facet — suitable for a
 * single `?f=` URL param or a localStorage blob. Returns empty string when
 * nothing is active.
 */
export function serializeFilterState<T>(
  facets: FacetDefinition<T>[],
  state: FilterState,
): string {
  const out: Record<string, string> = {};
  for (const f of facets) {
    const v = state[f.id];
    if (!v || isDefaultValue(f, v)) continue;
    out[f.id] = serializeValue(v);
  }
  return Object.keys(out).length === 0 ? '' : JSON.stringify(out);
}

/**
 * Parse a string produced by `serializeFilterState` back into a partial
 * FilterState. Only facets present in the `facets` array are recognized;
 * unknown keys are silently dropped (forward/backward compatible).
 */
export function parseFilterState<T>(
  facets: FacetDefinition<T>[],
  str: string,
): Partial<FilterState> {
  if (!str) return {};
  try {
    const obj = JSON.parse(str) as Record<string, string>;
    const out: Partial<FilterState> = {};
    for (const f of facets) {
      const raw = obj[f.id];
      if (raw === undefined) continue;
      const parsed = parseValue(f.kind, raw);
      if (parsed) out[f.id] = parsed;
    }
    return out;
  } catch {
    return {};
  }
}

export function useFilterState<T>(
  facets: FacetDefinition<T>[],
  opts?: {
    initialState?: Partial<FilterState>;
    /** When set, filter state is persisted to localStorage under this key. */
    storageKey?: string;
  },
): UseFilterStateResult<T> {
  const storageKey = opts?.storageKey;

  const [state, setState] = useState<FilterState>(() => {
    // Priority: explicit initialState > localStorage > defaults.
    if (opts?.initialState) return buildInitialState(facets, opts.initialState);
    if (storageKey) {
      try {
        const stored = window.localStorage.getItem(storageKey);
        if (stored) {
          const parsed = parseFilterState(facets, stored);
          if (Object.keys(parsed).length > 0) return buildInitialState(facets, parsed);
        }
      } catch { /* localStorage unavailable or corrupt — fall through */ }
    }
    return buildInitialState(facets);
  });

  // Ensure every current facet has a state entry. When the facets array
  // changes (e.g. catalog type switch), new facet ids are missing from the
  // initial state — seed them with defaults. Existing entries are preserved.
  useEffect(() => {
    setState((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const f of facets) {
        if (!(f.id in next)) {
          next[f.id] = defaultFilterValue(f);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [facets]);

  // Persist to localStorage whenever state changes (if storageKey is set).
  useEffect(() => {
    if (!storageKey) return;
    try {
      const str = serializeFilterState(facets, state);
      if (str) window.localStorage.setItem(storageKey, str);
      else window.localStorage.removeItem(storageKey);
    } catch { /* quota exceeded or unavailable — non-critical */ }
  }, [state, storageKey, facets]);

  const facetsById = useMemo(() => {
    const map = new Map<string, FacetDefinition<T>>();
    for (const f of facets) map.set(f.id, f);
    return map;
  }, [facets]);

  const set = useCallback((id: string, value: FilterValue) => {
    setState((prev) => ({ ...prev, [id]: value }));
  }, []);

  const toggle = useCallback(
    (id: string, optionValue: string) => {
      setState((prev) => {
        const facet = facetsById.get(id);
        if (!facet) return prev;
        const current = prev[id] ?? defaultFilterValue(facet);
        if (current.kind === 'multi') {
          const has = current.values.includes(optionValue);
          return {
            ...prev,
            [id]: {
              kind: 'multi',
              values: has
                ? current.values.filter((v) => v !== optionValue)
                : [...current.values, optionValue],
            },
          };
        }
        if (current.kind === 'single') {
          return {
            ...prev,
            [id]: {
              kind: 'single',
              value: current.value === optionValue ? null : optionValue,
            },
          };
        }
        return prev;
      });
    },
    [facetsById],
  );

  const clear = useCallback(
    (id: string) => {
      setState((prev) => {
        const facet = facetsById.get(id);
        if (!facet) return prev;
        return { ...prev, [id]: defaultFilterValue(facet) };
      });
    },
    [facetsById],
  );

  const clearAll = useCallback(() => {
    setState((prev) => {
      const next: FilterState = {};
      for (const [id, value] of Object.entries(prev)) {
        const facet = facetsById.get(id);
        next[id] = facet ? defaultFilterValue(facet) : value;
      }
      return next;
    });
  }, [facetsById]);

  const { isActive, activeCount } = useMemo(() => {
    let count = 0;
    for (const f of facets) {
      const v = state[f.id];
      if (v && !isDefaultValue(f, v)) count++;
    }
    return { isActive: count > 0, activeCount: count };
  }, [facets, state]);

  const applyFilters = useCallback(
    (items: T[]): T[] => {
      const active: Array<{ facet: FacetDefinition<T>; value: FilterValue }> = [];
      for (const f of facets) {
        if (f.mode !== 'client' || !f.predicate) continue;
        const v = state[f.id];
        if (!v || isDefaultValue(f, v)) continue;
        active.push({ facet: f, value: v });
      }
      if (active.length === 0) return items;
      return items.filter((item) => active.every(({ facet, value }) => facet.predicate!(item, value)));
    },
    [facets, state],
  );

  const matches = useCallback(
    (item: T): boolean => {
      for (const f of facets) {
        if (f.mode !== 'client' || !f.predicate) continue;
        const v = state[f.id];
        if (!v || isDefaultValue(f, v)) continue;
        if (!f.predicate(item, v)) return false;
      }
      return true;
    },
    [facets, state],
  );

  const serverParams = useMemo(() => {
    const params: Record<string, string> = {};
    for (const f of facets) {
      if (f.mode !== 'server' || !f.serverParam || !f.serverValueSerializer) continue;
      const v = state[f.id];
      if (!v || isDefaultValue(f, v)) continue;
      const serialized = f.serverValueSerializer(v);
      if (serialized === undefined) continue;
      if (Array.isArray(serialized)) {
        if (serialized.length > 0) params[f.serverParam] = serialized.join(',');
      } else {
        params[f.serverParam] = serialized;
      }
    }
    return params;
  }, [facets, state]);

  const serialize = useCallback((): UrlParams => {
    const out: UrlParams = {};
    for (const f of facets) {
      if (!f.syncToUrl) continue;
      const v = state[f.id];
      if (!v || isDefaultValue(f, v)) continue;
      out[f.urlKey ?? f.id] = serializeValue(v);
    }
    return out;
  }, [facets, state]);

  const parse = useCallback(
    (params: UrlParams): FilterState => {
      const out: FilterState = {};
      for (const f of facets) {
        if (!f.syncToUrl) continue;
        const raw = params[f.urlKey ?? f.id];
        if (raw === undefined) continue;
        const parsed = parseValue(f.kind, raw);
        if (parsed) out[f.id] = parsed;
      }
      return out;
    },
    [facets],
  );

  return {
    state,
    set,
    toggle,
    clear,
    clearAll,
    isActive,
    activeCount,
    applyFilters,
    matches,
    serverParams,
    serialize,
    parse,
  };
}
