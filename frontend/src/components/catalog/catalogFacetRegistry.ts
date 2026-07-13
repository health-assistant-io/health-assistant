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

export function getFacetsForType(type: string): FacetDefinition<CatalogItem>[] {
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
    default:
      return [];
  }
}
