/**
 * Vaccine (immunization) instance adapter.
 */
import type { InstanceAdapter, InstanceQuery, InstanceSearchHit } from '../../../components/instances/types';
import { getPatientImmunizations, getImmunization } from '../../../services/vaccineService';
import type { PatientImmunization } from '../../../types/vaccine';
import { searchInstances } from '../../../services/instanceSearchService';
import { normalizeResult } from './_shared';
import { getVaccineFacets } from '../facets/vaccineFacets';

const SEARCH_FIELDS = (v: PatientImmunization) => [v.vaccine_code?.text, v.lot_number];

export const vaccineAdapter: InstanceAdapter<PatientImmunization> = {
  type: 'vaccine',
  entityLabel: { singular: 'Vaccination', plural: 'Vaccinations' },
  icon: 'Syringe',

  async fetch(query: InstanceQuery) {
    if (!query.patientId) return { items: [], total: 0 };
    const items = await getPatientImmunizations(query.patientId);
    return normalizeResult(items, query, SEARCH_FIELDS);
  },

  async search(query: InstanceQuery): Promise<InstanceSearchHit[]> {
    return searchInstances({
      q: query.q ?? '',
      patientId: query.patientId,
      limit: query.limit,
      types: ['vaccine'],
    });
  },

  async fetchOne(id: string) {
    return getImmunization(id);
  },

  facets: getVaccineFacets(),

  toRow(v) {
    return {
      id: v.id,
      type: 'vaccine',
      label: v.vaccine_code?.text || 'Vaccination',
      subtitle: v.lot_number ? `Lot ${v.lot_number}` : undefined,
      description: v.note || undefined,
      date: v.administered_at ?? undefined,
      status: v.status,
      icon: 'Syringe',
      raw: v,
    };
  },

  toSelection(v) {
    return {
      type: 'vaccine',
      id: v.id,
      label: v.vaccine_code?.text || 'Vaccination',
    };
  },

  detailRoute() {
    return null;
  },
};
