/** Vaccine (immunization) instance facets — single source for adapter + listing. */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import type { PatientImmunization } from '../../../types/vaccine';
import { multiFacet } from './helpers';

export function getVaccineFacets(): FacetDefinition<PatientImmunization>[] {
  return [
    multiFacet(
      'status',
      'Status',
      (v) => v.status,
      { icon: 'CircleDot' },
    ),
  ];
}
