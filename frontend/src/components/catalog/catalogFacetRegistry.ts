/**
 * Maps a catalog type to its filter facets. Each type's facets are declared
 * in its own domain module (e.g. `features/biomarkers/facets`) and registered
 * here. Returns an empty array for types without facets yet — so the toolbar
 * only shows the FilterBar row when a type has facets.
 *
 * Facets are typed against their domain item shape (Biomarker, …) but exposed
 * here under the shared `CatalogItem` type for the polymorphic workspace. The
 * workspace only applies a type's facets while that type is active, and the
 * predicates access fields that exist on that type's items, so the cast is
 * safe at runtime.
 *
 * See `dev/plans/modular-filter-system-2026-07-14.md` §2 Phase 3.
 */
import type { FacetDefinition } from '../ui/filters';
import type { CatalogItem } from '../../types/catalog';
import { catalogBiomarkerFacets } from '../../features/biomarkers/facets';
import {
  catalogAllergyFacets,
  catalogVaccineFacets,
  catalogMedicationFacets,
  catalogConceptFacets,
} from './catalogFacets';
import { CLASS_COLOR } from '../../types/anatomy';

/**
 * Dynamic facet options the workspace can inject (e.g. the fetched
 * ``anatomy_class`` concepts). Kept separate from the pure facet definitions
 * so the registry stays a pure data layer except for these externally-fetched
 * option lists.
 */
export interface CatalogFacetContext {
  /** Anatomy-class concept options (fetched), for the anatomy ``class`` facet. */
  classOptions?: { slug: string; name: string }[];
}

/**
 * Server-side ``class`` facet for taxonomy-class filtering (anatomy — and any
 * other catalog whose items carry a ``class_concept_id`` FK). Options are
 * injected so the dropdown is complete regardless of the loaded page.
 */
function makeClassFacet(
  options: { slug: string; name: string }[],
): FacetDefinition<CatalogItem> {
  return {
    id: 'class',
    label: 'Class',
    kind: 'multi',
    mode: 'server',
    icon: 'Layers',
    options: options.map((c) => ({
      value: c.slug,
      label: c.name,
      color: CLASS_COLOR(c.slug),
    })),
    serverParam: 'class',
    serverValueSerializer: (value) => {
      if (value.kind !== 'multi' || value.values.length === 0) return undefined;
      return value.values;
    },
  };
}

export function getFacetsForType(
  type: string,
  ctx?: CatalogFacetContext,
): FacetDefinition<CatalogItem>[] {
  switch (type) {
    case 'biomarker':
      return catalogBiomarkerFacets as unknown as FacetDefinition<CatalogItem>[];
    case 'allergy':
      return catalogAllergyFacets;
    case 'vaccine':
      return catalogVaccineFacets;
    case 'medication':
      return catalogMedicationFacets;
    case 'concept':
      return catalogConceptFacets;
    case 'anatomy':
      return ctx?.classOptions ? [makeClassFacet(ctx.classOptions)] : [];
    default:
      return [];
  }
}
