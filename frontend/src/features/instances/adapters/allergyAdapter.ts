/**
 * Allergy instance adapter.
 */
import type { InstanceAdapter, InstanceQuery, InstanceSearchHit } from '../../../components/instances/types';
import { getPatientAllergies, type AllergyIntolerance } from '../../../services/allergyService';
import { searchInstances } from '../../../services/instanceSearchService';
import { normalizeResult } from './_shared';
import { getAllergyFacets } from '../facets/allergyFacets';

const SEARCH_FIELDS = (a: AllergyIntolerance) => [a.code?.text];

export const allergyAdapter: InstanceAdapter<AllergyIntolerance> = {
  type: 'allergy',
  entityLabel: { singular: 'Allergy', plural: 'Allergies' },
  icon: 'AlertTriangle',

  async fetch(query: InstanceQuery) {
    if (!query.patientId) return { items: [], total: 0 };
    const items = await getPatientAllergies(query.patientId);
    return normalizeResult(items, query, SEARCH_FIELDS);
  },

  async search(query: InstanceQuery): Promise<InstanceSearchHit[]> {
    return searchInstances({
      q: query.q ?? '',
      patientId: query.patientId,
      limit: query.limit,
      types: ['allergy'],
    });
  },

  // No domain getById endpoint for allergies — resolve from the patient list.
  async fetchOne(id: string, patientId?: string) {
    if (!patientId) throw new Error('allergy fetchOne requires a patientId');
    const items = await getPatientAllergies(patientId);
    const found = items.find((a) => a.id === id);
    if (!found) throw new Error(`Allergy ${id} not found`);
    return found;
  },

  facets: getAllergyFacets(),

  toRow(a) {
    const active = a.clinical_status === 'ACTIVE';
    return {
      id: a.id,
      type: 'allergy',
      label: a.code?.text || 'Allergy',
      subtitle: a.clinical_status,
      date: a.onset_date ?? undefined,
      status: a.clinical_status,
      statusColor: active ? '#dc2626' : a.criticality === 'HIGH' ? '#ea580c' : null,
      icon: 'AlertTriangle',
      raw: a,
    };
  },

  toSelection(a) {
    return { type: 'allergy', id: a.id, label: a.code?.text || 'Allergy' };
  },

  detailRoute() {
    return null;
  },
};
