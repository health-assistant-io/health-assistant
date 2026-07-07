import api from '../api/axios';
import type {
  Concept,
  ConceptEdge,
  ConceptCreateInput,
  ConceptUpdateInput,
  ConceptKind,
  NeighborResult,
} from '../types/concept';

export const listConcepts = async (params?: {
  kind?: ConceptKind;
  parent_id?: string;
  include_retired?: boolean;
  limit?: number;
  offset?: number;
}): Promise<Concept[]> => {
  const query = new URLSearchParams();
  if (params?.kind) query.set('kind', params.kind);
  if (params?.parent_id) query.set('parent_id', params.parent_id);
  if (params?.include_retired) query.set('include_retired', 'true');
  if (params?.limit) query.set('limit', String(params.limit));
  if (params?.offset) query.set('offset', String(params.offset));
  const qs = query.toString();
  const response = await api.get(`/concepts${qs ? `?${qs}` : ''}`);
  return response.data;
};

export const searchConcepts = async (
  q: string,
  kind?: ConceptKind,
  limit?: number,
): Promise<Concept[]> => {
  const query = new URLSearchParams({ q });
  if (kind) query.set('kind', kind);
  if (limit) query.set('limit', String(limit));
  const response = await api.get(`/concepts/search?${query.toString()}`);
  return response.data;
};

export const getConcept = async (id: string): Promise<Concept> => {
  const response = await api.get(`/concepts/${id}`);
  return response.data;
};

export const createConcept = async (
  data: ConceptCreateInput,
): Promise<Concept> => {
  const response = await api.post('/concepts', data);
  return response.data;
};

export const updateConcept = async (
  id: string,
  data: ConceptUpdateInput,
): Promise<Concept> => {
  const response = await api.put(`/concepts/${id}`, data);
  return response.data;
};

export const deleteConcept = async (id: string): Promise<void> => {
  await api.delete(`/concepts/${id}`);
};

export const getConceptNeighbors = async (
  id: string,
  params?: { relation?: string; include_proposed?: boolean },
): Promise<NeighborResult[]> => {
  const query = new URLSearchParams();
  if (params?.relation) query.set('relation', params.relation);
  if (params?.include_proposed) query.set('include_proposed', 'true');
  const qs = query.toString();
  const response = await api.get(
    `/concepts/${id}/neighbors${qs ? `?${qs}` : ''}`,
  );
  return response.data;
};

export const listEdges = async (params?: {
  src_type?: string;
  src_id?: string;
  dst_type?: string;
  dst_id?: string;
  relation?: string;
  include_proposed?: boolean;
  limit?: number;
}): Promise<ConceptEdge[]> => {
  const query = new URLSearchParams();
  if (params?.src_type) query.set('src_type', params.src_type);
  if (params?.src_id) query.set('src_id', params.src_id);
  if (params?.dst_type) query.set('dst_type', params.dst_type);
  if (params?.dst_id) query.set('dst_id', params.dst_id);
  if (params?.relation) query.set('relation', params.relation);
  if (params?.include_proposed) query.set('include_proposed', 'true');
  if (params?.limit) query.set('limit', String(params.limit));
  const qs = query.toString();
  const response = await api.get(
    `/concept-edges${qs ? `?${qs}` : ''}`,
  );
  return response.data;
};

export const createEdge = async (data: {
  src_type: string;
  src_id: string;
  dst_type: string;
  dst_id: string;
  relation: string;
  properties?: Record<string, any>;
  evidence?: Record<string, any>;
  source?: string;
  status?: string;
  tenant_scoped?: boolean;
}): Promise<ConceptEdge> => {
  const response = await api.post('/concept-edges', data);
  return response.data;
};

export const deleteEdge = async (id: string): Promise<void> => {
  await api.delete(`/concept-edges/${id}`);
};

// ---------------------------------------------------------------------------
// Module-level cache for document categories — replaces the hardcoded
// constants/categories.ts with a dynamic, seeded, user-editable source.
// Loaded once on app startup (see App.tsx), then accessed synchronously
// by hooks/components that need category labels during render.
// ---------------------------------------------------------------------------

let _docCategories: Concept[] = [];
let _docCategoryMap = new Map<string, string>();
let _docCategoryPromise: Promise<void> | null = null;

export async function loadDocumentCategories(): Promise<void> {
  if (_docCategoryPromise) return _docCategoryPromise;
  _docCategoryPromise = (async () => {
    try {
      const concepts = await listConcepts({ kind: 'document_category', limit: 100 });
      _docCategories = concepts;
      _docCategoryMap = new Map(concepts.map((c) => [c.slug, c.name]));
    } catch {
      // App still works with empty cache; categories load on next navigation
    }
  })();
  return _docCategoryPromise;
}

export function getDocumentCategories(): Concept[] {
  return _docCategories;
}

export function getDocumentCategoryLabel(slug: string): string {
  const label = _docCategoryMap.get(slug);
  if (label) return label;
  return slug
    .split(/[_-]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ');
}

export function invalidateDocumentCategories(): void {
  _docCategoryPromise = null;
  _docCategories = [];
  _docCategoryMap = new Map();
}
