/**
 * Catalog facets for non-biomarker types. These types don't have dedicated
 * `features/` directories, so their facets live here alongside the registry.
 *
 * Fields are accessed via CatalogItem's index signature (`Record<string,
 * unknown>`) — the `strField` helper safely extracts string fields, and
 * boolean toggles use strict `=== true` comparison.
 *
 * See `dev/plans/modular-filter-system-2026-07-14.md` §2 Phase 6c.
 */
import type { FacetDefinition } from '../ui/filters';
import { deriveCountedOptions } from '../ui/filters';
import type { CatalogItem } from '../../types/catalog';

function strField(field: string): (item: CatalogItem) => string | undefined {
  return (item) => {
    const v = item[field];
    return typeof v === 'string' ? v : undefined;
  };
}

// ── Allergy ──────────────────────────────────────────────────────────────

export const allergyCategoryFacet: FacetDefinition<CatalogItem> = {
  id: 'category',
  label: 'Category',
  kind: 'multi',
  mode: 'client',
  icon: 'ShieldAlert',
  getOptions: (items) => deriveCountedOptions(items, strField('category')),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    const cat = strField('category')(item);
    return cat !== undefined && value.values.includes(cat);
  },
};

export const allergyCustomFacet: FacetDefinition<CatalogItem> = {
  id: 'is_custom',
  label: 'Custom only',
  kind: 'toggle',
  mode: 'client',
  icon: 'Pencil',
  predicate: (item, value) => {
    if (value.kind !== 'toggle' || !value.on) return true;
    return item['is_custom'] === true;
  },
};

export const catalogAllergyFacets: FacetDefinition<CatalogItem>[] = [
  allergyCategoryFacet,
  allergyCustomFacet,
];

// ── Vaccine ──────────────────────────────────────────────────────────────

export const vaccineCodingSystemFacet: FacetDefinition<CatalogItem> = {
  id: 'coding_system',
  label: 'Coding system',
  kind: 'multi',
  mode: 'client',
  icon: 'Barcode',
  getOptions: (items) => deriveCountedOptions(items, strField('coding_system'), (v) => v.toUpperCase()),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    const cs = strField('coding_system')(item);
    return cs !== undefined && value.values.includes(cs);
  },
};

export const catalogVaccineFacets: FacetDefinition<CatalogItem>[] = [
  vaccineCodingSystemFacet,
];

// ── Medication ───────────────────────────────────────────────────────────

export const medicationCustomFacet: FacetDefinition<CatalogItem> = {
  id: 'is_custom',
  label: 'Custom only',
  kind: 'toggle',
  mode: 'client',
  icon: 'Pencil',
  predicate: (item, value) => {
    if (value.kind !== 'toggle' || !value.on) return true;
    return item['is_custom'] === true;
  },
};

export const catalogMedicationFacets: FacetDefinition<CatalogItem>[] = [
  medicationCustomFacet,
];

// ── Concept ──────────────────────────────────────────────────────────────

export const conceptStatusFacet: FacetDefinition<CatalogItem> = {
  id: 'status',
  label: 'Status',
  kind: 'multi',
  mode: 'client',
  icon: 'CircleDot',
  getOptions: (items) => deriveCountedOptions(items, strField('status'), (v) => v.charAt(0).toUpperCase() + v.slice(1)),
  predicate: (item, value) => {
    if (value.kind !== 'multi' || value.values.length === 0) return true;
    const st = strField('status')(item);
    return st !== undefined && value.values.includes(st);
  },
};

export const catalogConceptFacets: FacetDefinition<CatalogItem>[] = [
  conceptStatusFacet,
];
