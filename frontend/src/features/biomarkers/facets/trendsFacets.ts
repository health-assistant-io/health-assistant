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

/** Classify a biomarker by where it came from.
 *  - `system`      → telemetry / IoT vital signs (`isTelemetry`)
 *  - `examination` → produced under an examination (`source.examinationId`)
 *  - `technical`   → lab / imaging / technical document (everything else)
 *  Priority is telemetry > examination > technical (a telemetry row is never
 *  also tagged examination, even if it carries an exam id). */
export function biomarkerSourceType(b: BiomarkerObservation): string {
  if (b.isTelemetry) return 'system';
  if (b.source.examinationId) return 'examination';
  return 'technical';
}

const SOURCE_TYPE_LABEL: Record<string, string> = {
  system: 'System',
  technical: 'Technical',
  examination: 'Examination',
};

/** Filter by origin: system (telemetry) / technical (lab/imaging) / examination. */
export const biomarkerSourceTypeFacet: FacetDefinition<BiomarkerObservation> = {
  id: 'source_type',
  label: 'Source type',
  kind: 'multi',
  mode: 'client',
  icon: 'Layers',
  getOptions: (items) =>
    deriveCountedOptions(items, biomarkerSourceType, (v) => SOURCE_TYPE_LABEL[v] ?? v),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    return value.values.includes(biomarkerSourceType(item));
  },
};

/** Filter by the technical subcategory (lab panel, imaging, …) carried on the
 *  raw row as `techCategory` / `document_category`. */
export const biomarkerSubcategoryFacet: FacetDefinition<BiomarkerObservation> = {
  id: 'subcategory',
  label: 'Subcategory',
  kind: 'multi',
  mode: 'client',
  icon: 'FolderTree',
  getOptions: (items) =>
    deriveCountedOptions(
      items,
      (b) => b._rawJson?.techCategory || b._rawJson?.document_category,
    ),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    const sub = item._rawJson?.techCategory || item._rawJson?.document_category;
    return sub !== undefined && value.values.includes(sub);
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
  biomarkerSourceTypeFacet,
  biomarkerSubcategoryFacet,
  biomarkerUnitObsFacet,
  biomarkerSourceFacet,
  biomarkerMappedFacet,
];
