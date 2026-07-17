/** Allergy instance facets — single source for the adapter + listing. */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import type { AllergyIntolerance } from '../../../services/allergyService';
import { categoryFacet, multiFacet, type CategoryFacetCtx } from './helpers';

export function getAllergyFacets(
  ctx?: CategoryFacetCtx,
): FacetDefinition<AllergyIntolerance>[] {
  return [
    multiFacet(
      'clinical_status',
      'Status',
      (a) => a.clinical_status,
      { icon: 'CircleDot' },
    ),
    categoryFacet(
      'category',
      'Category',
      (a) => a.category,
      ctx,
      { icon: 'AlertTriangle' },
    ),
  ];
}
