/**
 * Observation (biomarker result) instance facets — single source for the
 * adapter + browse modal. Mirrors the `/analytics/trends` filter set as far as
 * the raw Observation shape allows:
 *   - status — recomputed via `getObservationStatus` (the SAME algorithm as
 *     `getFinalStatus` on the enriched BiomarkerObservation: range-based, then
 *     interpretation fallback). Single source of truth for status logic.
 *   - unit — normalized_unit, falling back to value_quantity.unit.
 *   - mapped — hide observations not linked to a biomarker definition.
 *
 * `telemetry` and `source`/lab are enrichment fields produced by `useBiomarkers`
 * (not present on the raw ORM row), so they stay trends-only.
 */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import type { Observation } from '../../../types/observation';
import { getObservationStatus } from '../../../utils/biomarkerUtils';
import { multiFacet, toggleFacet } from './helpers';

export function getObservationFacets(): FacetDefinition<Observation>[] {
  return [
    multiFacet(
      'status',
      'Status',
      (o) => getObservationStatus(o),
      { icon: 'Activity' },
    ),
    multiFacet(
      'unit',
      'Unit',
      (o) => o.normalized_unit || o.value_quantity?.unit,
      { icon: 'Ruler' },
    ),
    toggleFacet(
      'mapped',
      'Hide unmapped',
      (o) => !!o.biomarker_id,
      { icon: 'Map' },
    ),
  ];
}
