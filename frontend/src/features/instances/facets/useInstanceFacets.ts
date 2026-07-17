/**
 * `useInstanceFacets` — bind the shared instance facet registry to a view in
 * one call. Per-type browse Views (`ExaminationView`, `ObservationView`, …)
 * take over the browse modal and therefore own their own filter bar; this hook
 * gives them the SAME facets the generic `InstanceBrowser` path uses (single
 * source of truth) plus the filtered item list.
 */
import { useMemo } from 'react';
import { useFilterState } from '../../../components/ui/filters';
import type { InstanceType } from '../../../components/instances/types';
import { getInstanceFacets, type CategoryFacetCtx } from './index';

export function useInstanceFacets<T>(
  type: InstanceType | string,
  items: T[],
  ctx?: CategoryFacetCtx,
): {
  facets: ReturnType<typeof getInstanceFacets>;
  filter: ReturnType<typeof useFilterState<T>>;
  filtered: T[];
} {
  const facets = useMemo(() => getInstanceFacets(type, ctx), [type, ctx]);
  const filter = useFilterState<T>(facets as any);
  const filtered = useMemo(() => filter.applyFilters(items), [filter, items]);
  return { facets, filter, filtered };
}
