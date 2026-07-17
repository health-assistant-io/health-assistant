/** Clinical-event instance facets — single source for the adapter + listing page. */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import type { ClinicalEvent } from '../../../services/clinicalEventService';
import { categoryFacet, multiFacet, type CategoryFacetCtx } from './helpers';

export function getEventFacets(
  ctx?: CategoryFacetCtx,
): FacetDefinition<ClinicalEvent>[] {
  return [
    categoryFacet(
      'category',
      'Category',
      (e) => e.type_details?.category_concept_id,
      ctx,
      { icon: 'Activity' },
    ),
    multiFacet(
      'status',
      'Status',
      (e) => e.status,
      { icon: 'CircleDot' },
    ),
  ];
}
