/**
 * Observation (biomarker result) instance adapter.
 *
 * Browse via ``listObservations`` (the only paginated instance service).
 * Search via the unified dispatcher scoped to ``observation``. No dedicated
 * domain route (observations are viewed in biomarker/examination detail).
 */
import type { InstanceAdapter, InstanceQuery, InstanceSearchHit } from '../../../components/instances/types';
import { listObservations, getObservation } from '../../../services/observationService';
import { searchInstances } from '../../../services/instanceSearchService';
import { clientFilter } from './_shared';
import { getObservationStatus } from '../../../utils/biomarkerUtils';
import { getObservationFacets } from '../facets/observationFacets';
import type { Observation } from '../../../types/observation';

const SEARCH_FIELDS = (o: Observation) => [o.code?.text, o.value_string];

export const observationAdapter: InstanceAdapter<Observation> = {
  type: 'observation',
  entityLabel: { singular: 'Observation', plural: 'Observations' },
  icon: 'Activity',

  async fetch(query: InstanceQuery) {
    const result = await listObservations(
      undefined,
      query.patientId,
      undefined,
      undefined,
      undefined,
      query.limit,
      query.offset,
    );
    const filtered = clientFilter(result.items, query.q, SEARCH_FIELDS);
    return { items: filtered, total: filtered.length };
  },

  async search(query: InstanceQuery): Promise<InstanceSearchHit[]> {
    return searchInstances({
      q: query.q ?? '',
      patientId: query.patientId,
      limit: query.limit,
      types: ['observation'],
    });
  },

  async fetchOne(id: string) {
    return getObservation(id);
  },

  facets: getObservationFacets(),

  toRow(o) {
    const vq = o.value_quantity;
    const value = vq ? `${vq.value}${vq.unit ? ' ' + vq.unit : ''}` : o.value_string;
    const label =
      o.code?.text ||
      o.code?.coding?.[0]?.display ||
      o.biomarker_slug ||
      'Observation';
    // Status recomputed via the shared `getObservationStatus` (same algorithm
    // as trends' `getFinalStatus`) — the stored interpretation can be stale.
    const interpretation = getObservationStatus(o);
    return {
      id: o.id,
      type: 'observation',
      label,
      subtitle: value || undefined,
      description: o.comment || undefined,
      date: o.effective_datetime,
      status: interpretation,
      statusColor:
        interpretation === 'High'
          ? '#dc2626'
          : interpretation === 'Low'
            ? '#dc2626'
            : interpretation === 'Normal'
              ? '#16a34a'
              : null,
      icon: 'Activity',
      raw: o,
    };
  },

  toSelection(o) {
    return {
      type: 'observation',
      id: o.id,
      label:
        o.code?.text ||
        o.code?.coding?.[0]?.display ||
        o.biomarker_slug ||
        'Observation',
    };
  },

  detailRoute() {
    return null;
  },
};
