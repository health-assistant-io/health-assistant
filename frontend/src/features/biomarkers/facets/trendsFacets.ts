import type { FacetDefinition } from '../../../components/ui/filters';
import type { BiomarkerObservation } from '../../../types/biomarker';
import { getFinalStatus } from '../../../utils/biomarkerUtils';
import { deriveCountedOptions } from './helpers';

/**
 * Trends biomarker facets — `FacetDefinition<BiomarkerObservation>[]` for the
 * `/analytics/trends` view (last biomarker results). Pure data/functions, no
 * JSX. Reuses `getFinalStatus` so the status facet is consistent with the
 * existing alerts-only toggle and badge coloring.
 *
 * See `dev/plans/modular-filter-system-2026-07-14.md` §2 Phase 2.
 */

/** Filter by computed status: Normal / High / Low (via `getFinalStatus`). */
export const biomarkerStatusFacet: FacetDefinition<BiomarkerObservation> = {
  id: 'status',
  label: 'Status',
  kind: 'multi',
  mode: 'client',
  icon: 'Activity',
  getOptions: (items) => deriveCountedOptions(items, (b) => getFinalStatus(b)),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    return value.values.includes(getFinalStatus(item));
  },
};

/** Show only telemetry (IoT) observations. */
export const biomarkerTelemetryObsFacet: FacetDefinition<BiomarkerObservation> = {
  id: 'telemetry',
  label: 'Telemetry only',
  kind: 'toggle',
  mode: 'client',
  icon: 'Radio',
  predicate: (item, value) => {
    if (value.kind !== 'toggle' || !value.on) return true;
    return item.isTelemetry === true;
  },
};

/** Filter by normalized (or raw) unit symbol. */
export const biomarkerUnitObsFacet: FacetDefinition<BiomarkerObservation> = {
  id: 'unit',
  label: 'Unit',
  kind: 'multi',
  mode: 'client',
  icon: 'Ruler',
  getOptions: (items) => deriveCountedOptions(items, (b) => b.unit.normalizedSymbol ?? b.unit.rawSymbol),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    return value.values.includes(item.unit.normalizedSymbol ?? item.unit.rawSymbol);
  },
};

/** Filter by lab / source name. */
export const biomarkerSourceFacet: FacetDefinition<BiomarkerObservation> = {
  id: 'source',
  label: 'Lab / source',
  kind: 'multi',
  mode: 'client',
  icon: 'FlaskConical',
  getOptions: (items) => deriveCountedOptions(items, (b) => b.source.labName),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    return item.source.labName !== undefined && value.values.includes(item.source.labName);
  },
};

/** Hide biomarkers that could not be mapped to a definition. */
export const biomarkerMappedFacet: FacetDefinition<BiomarkerObservation> = {
  id: 'mapped',
  label: 'Hide unmapped',
  kind: 'toggle',
  mode: 'client',
  icon: 'Map',
  predicate: (item, value) => {
    if (value.kind !== 'toggle' || !value.on) return true;
    return !item.isUnmapped;
  },
};

/** All trends biomarker facets, in toolbar display order. */
export const trendsBiomarkerFacets: FacetDefinition<BiomarkerObservation>[] = [
  biomarkerStatusFacet,
  biomarkerTelemetryObsFacet,
  biomarkerUnitObsFacet,
  biomarkerSourceFacet,
  biomarkerMappedFacet,
];
