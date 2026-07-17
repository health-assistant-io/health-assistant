/**
 * Clinical event instance adapter.
 */
import type { InstanceAdapter, InstanceQuery, InstanceSearchHit } from '../../../components/instances/types';
import { getPatientEvents, getEvent, type ClinicalEvent } from '../../../services/clinicalEventService';
import { searchInstances } from '../../../services/instanceSearchService';
import { toSnippet } from '../../../utils/textFormat';
import { normalizeResult } from './_shared';
import { getEventFacets } from '../facets/eventFacets';

const SEARCH_FIELDS = (e: ClinicalEvent) => [e.title, e.description];

export const eventAdapter: InstanceAdapter<ClinicalEvent> = {
  type: 'event',
  entityLabel: { singular: 'Clinical event', plural: 'Clinical events' },
  icon: 'CalendarClock',

  async fetch(query: InstanceQuery) {
    if (!query.patientId) return { items: [], total: 0 };
    const items = await getPatientEvents(query.patientId);
    return normalizeResult(items, query, SEARCH_FIELDS);
  },

  async search(query: InstanceQuery): Promise<InstanceSearchHit[]> {
    return searchInstances({
      q: query.q ?? '',
      patientId: query.patientId,
      limit: query.limit,
      types: ['event'],
    });
  },

  async fetchOne(id: string) {
    return getEvent(id);
  },

  facets: getEventFacets(),

  toRow(e) {
    return {
      id: e.id,
      type: 'event',
      label: e.title || 'Clinical event',
      // description may be HTML / Markdown — plain snippet for the one-liner.
      subtitle: toSnippet(e.description, 100),
      description: e.description || undefined,
      date: e.onset_date,
      status: e.status,
      icon: 'CalendarClock',
      raw: e,
    };
  },

  toSelection(e) {
    return { type: 'event', id: e.id, label: e.title || 'Clinical event' };
  },

  detailRoute(e) {
    return `/events/${e.id}`;
  },
};
