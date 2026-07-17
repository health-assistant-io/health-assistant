/**
 * Document instance adapter.
 *
 * ``getDocuments`` returns the caller's documents tenant-wide (no patient
 * filter on the backend route — a known audit gap). This adapter enforces
 * patient scoping client-side (filtering by ``patient_id``) so the picker
 * never surfaces another patient's documents; the unified search endpoint
 * closes the gap server-side for type-ahead.
 */
import type { InstanceAdapter, InstanceQuery, InstanceSearchHit } from '../../../components/instances/types';
import { getDocuments, getDocument } from '../../../services/documentService';
import { searchInstances } from '../../../services/instanceSearchService';
import { clientFilter } from './_shared';
import { getDocumentFacets } from '../facets/documentFacets';

interface DocumentInstance {
  id: string;
  filename: string;
  status: string;
  created_at: string;
  patient_id?: string;
  examination_id?: string;
}

const SEARCH_FIELDS = (d: DocumentInstance) => [d.filename];

export const documentAdapter: InstanceAdapter<DocumentInstance> = {
  type: 'document',
  entityLabel: { singular: 'Document', plural: 'Documents' },
  icon: 'FileText',

  async fetch(query: InstanceQuery) {
    const all = await getDocuments();
    // Client-side patient scoping (the backend list route lacks it).
    const scoped = query.patientId
      ? all.filter((d) => d.patient_id === query.patientId)
      : all;
    const filtered = clientFilter(scoped as DocumentInstance[], query.q, SEARCH_FIELDS);
    return { items: filtered, total: filtered.length };
  },

  async search(query: InstanceQuery): Promise<InstanceSearchHit[]> {
    return searchInstances({
      q: query.q ?? '',
      patientId: query.patientId,
      limit: query.limit,
      types: ['document'],
    });
  },

  async fetchOne(id: string) {
    return (await getDocument(id)) as DocumentInstance;
  },

  facets: getDocumentFacets(),

  toRow(d) {
    return {
      id: d.id,
      type: 'document',
      label: d.filename,
      subtitle: d.status,
      date: d.created_at,
      status: d.status,
      icon: 'FileText',
      raw: d,
    };
  },

  toSelection(d) {
    return { type: 'document', id: d.id, label: d.filename };
  },

  detailRoute(d) {
    return `/documents/${d.id}`;
  },
};
