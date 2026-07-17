/** Medication instance facets — single source for the adapter + listing page. */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import type { MedicationRecord } from '../../../services/medicationService';
import { multiFacet } from './helpers';

export function getMedicationFacets(): FacetDefinition<MedicationRecord>[] {
  return [
    multiFacet(
      'status',
      'Status',
      (m) => m.status,
      { icon: 'CircleDot', labelFn: (v) => v.replace('-', ' ') },
    ),
  ];
}
