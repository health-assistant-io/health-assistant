/**
 * Examination instance adapter.
 *
 * Browse via ``getExaminations`` (paginated); search via the unified
 * ``/instances/search`` dispatcher scoped to ``examination``.
 */
import type { InstanceAdapter, InstanceQuery, InstanceSearchHit } from '../../../components/instances/types';
import { getExaminations, getExamination } from '../../../services/examinationService';
import { searchInstances } from '../../../services/instanceSearchService';
import { toSnippet } from '../../../utils/textFormat';
import { normalizeResult } from './_shared';
import { getExaminationFacets } from '../facets/examinationFacets';
import type { Examination } from '../../../types/clinical';

const SEARCH_FIELDS = (e: Examination) => [
  e.notes,
  e.patient_notes,
  e.category_concept?.name,
  e.category,
];

export const examinationAdapter: InstanceAdapter<Examination> = {
  type: 'examination',
  entityLabel: { singular: 'Examination', plural: 'Examinations' },
  icon: 'Stethoscope',

  async fetch(query: InstanceQuery) {
    const items = await getExaminations(query.patientId, query.limit, query.offset);
    return normalizeResult(items as Examination[], query, SEARCH_FIELDS);
  },

  async search(query: InstanceQuery): Promise<InstanceSearchHit[]> {
    return searchInstances({
      q: query.q ?? '',
      patientId: query.patientId,
      limit: query.limit,
      types: ['examination'],
    });
  },

  async fetchOne(id: string) {
    return (await getExamination(id)) as Examination;
  },

  facets: getExaminationFacets(),

  toRow(e) {
    return {
      id: e.id,
      type: 'examination',
      label: e.category_concept?.name || e.category || 'Examination',
      // notes are HTML (Quill) — collapse to a plain snippet for the one-line
      // subtitle. Full rich notes stay in `description` (FormattedText).
      subtitle: toSnippet(e.notes || e.patient_notes, 100),
      // Clinician notes are HTML (Quill); impressions are Markdown. Both render
      // via FormattedText in the preview pane.
      description: e.notes || e.impressions || e.patient_notes || undefined,
      date: e.examination_date,
      status: e.extraction_status,
      icon: 'Stethoscope',
      raw: e,
    };
  },

  toSelection(e) {
    return {
      type: 'examination',
      id: e.id,
      label: e.category_concept?.name || e.category || 'Examination',
    };
  },

  detailRoute(e) {
    return `/examinations/${e.id}`;
  },
};
