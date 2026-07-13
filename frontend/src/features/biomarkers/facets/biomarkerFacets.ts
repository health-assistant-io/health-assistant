import type { FacetDefinition } from '../../../components/ui/filters';
import type { Biomarker } from '../../../types/biomarker';
import { deriveCountedOptions } from './helpers';

/**
 * Catalog biomarker facets — `FacetDefinition<Biomarker>[]` for the
 * `/catalogs?type=biomarker` view. Pure data/functions, no JSX, so both the
 * catalog toolbar and unit tests can consume them without rendering.
 *
 * See `dev/plans/modular-filter-system-2026-07-14.md` §2 Phase 2.
 */

/** Filter by biomarker `category` (e.g. Lipids, Glucose, Hormones). */
export const biomarkerCategoryFacet: FacetDefinition<Biomarker> = {
  id: 'category',
  label: 'Category',
  kind: 'multi',
  mode: 'client',
  icon: 'FolderTree',
  getOptions: (items) => deriveCountedOptions(items, (b) => b.category),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    return item.category !== undefined && value.values.includes(item.category);
  },
};

/** Show only telemetry (IoT) biomarkers. */
export const biomarkerTelemetryFacet: FacetDefinition<Biomarker> = {
  id: 'is_telemetry',
  label: 'Telemetry only',
  kind: 'toggle',
  mode: 'client',
  icon: 'Activity',
  predicate: (item, value) => {
    if (value.kind !== 'toggle' || !value.on) return true;
    return item.is_telemetry === true;
  },
};

/** Filter by coding system (LOINC, SNOMED, custom). */
export const biomarkerCodingSystemFacet: FacetDefinition<Biomarker> = {
  id: 'coding_system',
  label: 'Coding system',
  kind: 'multi',
  mode: 'client',
  icon: 'Barcode',
  getOptions: (items) => deriveCountedOptions(items, (b) => b.coding_system, (v) => v.toUpperCase()),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    return item.coding_system !== undefined && value.values.includes(item.coding_system);
  },
};

/** Filter by preferred unit symbol (e.g. mg/dL, mmol/L). */
export const biomarkerUnitFacet: FacetDefinition<Biomarker> = {
  id: 'unit',
  label: 'Unit',
  kind: 'multi',
  mode: 'client',
  icon: 'Ruler',
  getOptions: (items) => deriveCountedOptions(items, (b) => b.preferred_unit_symbol),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    return item.preferred_unit_symbol !== undefined && value.values.includes(item.preferred_unit_symbol);
  },
};

/** All catalog biomarker facets, in toolbar display order. */
export const catalogBiomarkerFacets: FacetDefinition<Biomarker>[] = [
  biomarkerCategoryFacet,
  biomarkerTelemetryFacet,
  biomarkerCodingSystemFacet,
  biomarkerUnitFacet,
];
