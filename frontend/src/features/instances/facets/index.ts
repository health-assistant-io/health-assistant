/**
 * Instance facet registry dispatcher. Resolves the shared facet set for an
 * instance type — consumed by the instance adapters (→ InstanceBrowseModal /
 * InstancePicker) and the main listing pages, so each entity's filters are
 * defined in exactly one place.
 */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import type { InstanceType } from '../../../components/instances/types';
import type { CategoryFacetCtx } from './helpers';
import { getExaminationFacets } from './examinationFacets';
import { getMedicationFacets } from './medicationFacets';
import { getObservationFacets } from './observationFacets';
import { getEventFacets } from './eventFacets';
import { getVaccineFacets } from './vaccineFacets';
import { getAllergyFacets } from './allergyFacets';
import { getDocumentFacets } from './documentFacets';

export { getExaminationFacets } from './examinationFacets';
export { getMedicationFacets } from './medicationFacets';
export { getObservationFacets } from './observationFacets';
export { getEventFacets } from './eventFacets';
export { getVaccineFacets } from './vaccineFacets';
export { getAllergyFacets } from './allergyFacets';
export { getDocumentFacets } from './documentFacets';
export type { CategoryFacetCtx } from './helpers';

/**
 * Resolve the shared facets for an instance type. `ctx` forwards category
 * options to entities whose category facet supports them.
 *
 * Returns `FacetDefinition<any>[]` — the registry is polymorphic across entity
 * types; callers (adapter / page) retain their concrete `T` at the import site.
 */
export function getInstanceFacets(
  type: InstanceType | string,
  ctx?: CategoryFacetCtx,
): FacetDefinition<any>[] {
  switch (type) {
    case 'examination':
      return getExaminationFacets(ctx);
    case 'medication':
      return getMedicationFacets();
    case 'observation':
      return getObservationFacets();
    case 'event':
      return getEventFacets(ctx);
    case 'vaccine':
      return getVaccineFacets();
    case 'allergy':
      return getAllergyFacets(ctx);
    case 'document':
      return getDocumentFacets();
    default:
      return [];
  }
}
