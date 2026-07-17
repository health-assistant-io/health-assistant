/** Examination instance facets — single source for the adapter + listing page. */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import type { Examination } from '../../../types/clinical';
import { getExamCategory } from '../../../utils/examinationUtils';
import { categoryFacet, multiFacet, type CategoryFacetCtx } from './helpers';

export function getExaminationFacets(
  ctx?: CategoryFacetCtx,
): FacetDefinition<Examination>[] {
  return [
    categoryFacet(
      'category',
      'Category',
      (e) => getExamCategory(e),
      ctx,
      { icon: 'Stethoscope' },
    ),
    multiFacet(
      'status',
      'Status',
      (e) => e.extraction_status,
      { icon: 'CircleDot' },
    ),
  ];
}
