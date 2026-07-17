/**
 * Document instance facets — single source for the adapter. Only `status` lives
 * here; the document *category* facet is page-specific (it depends on
 * `entities.document_category` + an exam-id→category map built in
 * `DocumentList`), so `DocumentList` keeps that one inline and concats it after
 * `getDocumentFacets()`.
 */
import type { FacetDefinition } from '../../../components/ui/filters/types';
import { multiFacet } from './helpers';

// Document rows are structurally typed (see documentAdapter); mirror the shape.
interface DocumentInstance {
  status: string;
}

export function getDocumentFacets(): FacetDefinition<DocumentInstance>[] {
  return [
    multiFacet(
      'status',
      'Status',
      (d) => d.status,
      { icon: 'CircleDot' },
    ),
  ];
}
