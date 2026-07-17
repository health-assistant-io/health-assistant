/**
 * Medication instance adapter.
 *
 * Browse via ``getPatientMedications`` (full per-patient list, client-filtered
 * by ``q`` — the backend route has no search param yet). Search via the
 * unified dispatcher scoped to ``medication``.
 */
import type { InstanceAdapter, InstanceQuery, InstanceSearchHit } from '../../../components/instances/types';
import { getPatientMedications, getMedication, type MedicationRecord } from '../../../services/medicationService';
import { searchInstances } from '../../../services/instanceSearchService';
import { normalizeResult } from './_shared';
import { getMedicationFacets } from '../facets/medicationFacets';

const SEARCH_FIELDS = (m: MedicationRecord) => [m.code?.text, m.reason, m.dosage];

export const medicationAdapter: InstanceAdapter<MedicationRecord> = {
  type: 'medication',
  entityLabel: { singular: 'Medication', plural: 'Medications' },
  icon: 'Pill',

  async fetch(query: InstanceQuery) {
    if (!query.patientId) return { items: [], total: 0 };
    const items = await getPatientMedications(query.patientId);
    return normalizeResult(items, query, SEARCH_FIELDS);
  },

  async search(query: InstanceQuery): Promise<InstanceSearchHit[]> {
    return searchInstances({
      q: query.q ?? '',
      patientId: query.patientId,
      limit: query.limit,
      types: ['medication'],
    });
  },

  async fetchOne(id: string) {
    return getMedication(id);
  },

  facets: getMedicationFacets(),

  toRow(m) {
    return {
      id: m.id,
      type: 'medication',
      label: m.code?.text || 'Medication',
      subtitle: [m.dosage, m.reason].filter(Boolean).join(' · ') || undefined,
      description: m.reason || m.note || undefined,
      date: m.start_date,
      status: m.status,
      statusColor: m.status === 'active' ? '#16a34a' : m.status === 'stopped' ? '#dc2626' : null,
      icon: 'Pill',
      raw: m,
    };
  },

  toSelection(m) {
    return { type: 'medication', id: m.id, label: m.code?.text || 'Medication' };
  },

  detailRoute(m) {
    return `/medications/details/${m.id}`;
  },
};
