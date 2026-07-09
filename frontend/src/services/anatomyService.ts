import api from '../api/axios';
import type {
  AnatomyStructure,
  AnatomyGraphNode,
  AnatomyRelatedResponse,
  AnatomyImportPayload,
  AnatomyListResponse,
  AnatomyCategory,
  AnatomyDisplay,
  AnatomyFigure,
  AnatomyGraphResponse,
} from '../types/anatomy';

const BASE = '/anatomy';

export interface AnatomyListParams {
  category?: AnatomyCategory;
  /** Anatomy-class concept slug(s), e.g. ``organ`` or ``organ,organ-part``.
   * Replaces the legacy uppercase ``category`` (which the backend ignores). */
  class?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export interface AnatomyStructureInput {
  name: string;
  slug: string;
  category: AnatomyCategory;
  standard_system?: 'loinc' | 'snomed' | 'custom' | null;
  standard_code?: string | null;
  description?: string | null;
  is_custom?: boolean;
}

export interface AnatomyStructurePatch {
  name?: string;
  slug?: string;
  category?: AnatomyCategory;
  standard_system?: 'loinc' | 'snomed' | 'custom' | null;
  standard_code?: string | null;
  description?: string | null;
  is_custom?: boolean;
  display?: AnatomyDisplay | null;
}

export const anatomyService = {
  async list(params: AnatomyListParams = {}): Promise<AnatomyListResponse> {
    const { category, class: cls, search, limit = 500, offset = 0 } = params;
    const query: Record<string, unknown> = { limit, offset };
    if (cls) query.class = cls;
    else if (category) query.category = category; // legacy (backend ignores)
    if (search && search.trim()) query.search = search.trim();
    const res = await api.get(BASE, { params: query });
    return res.data;
  },

  async get(identifier: string): Promise<AnatomyGraphNode> {
    const res = await api.get(`${BASE}/${identifier}`);
    return res.data;
  },

  async getRelated(identifier: string): Promise<AnatomyRelatedResponse> {
    const res = await api.get(`${BASE}/${identifier}/related`);
    return res.data;
  },

  async getGraph(identifier: string, depth = 1): Promise<AnatomyGraphResponse> {
    const res = await api.get(`${BASE}/${identifier}/graph`, { params: { depth } });
    return res.data;
  },

  async create(data: AnatomyStructureInput): Promise<AnatomyStructure> {
    const res = await api.post(BASE, data);
    return res.data;
  },

  async update(identifier: string, data: AnatomyStructurePatch): Promise<AnatomyStructure> {
    const res = await api.patch(`${BASE}/${identifier}`, data);
    return res.data;
  },

  async remove(identifier: string): Promise<void> {
    await api.delete(`${BASE}/${identifier}`);
  },

  async createRelation(sourceId: string, targetId: string, relationType: string): Promise<void> {
    await api.post(`${BASE}/relations`, {
      source_id: sourceId,
      target_id: targetId,
      relation_type: relationType,
    });
  },

  async importGraph(payload: AnatomyImportPayload): Promise<Record<string, number>> {
    const res = await api.post(`${BASE}/import`, payload);
    return res.data;
  },

  // --- Figures (DB-driven body atlas, raster images) ---

  async listFigures(activeOnly = true): Promise<AnatomyFigure[]> {
    const res = await api.get(`${BASE}/figures`, { params: { active_only: activeOnly } });
    return res.data;
  },

  async getFigure(slug: string): Promise<AnatomyFigure> {
    const res = await api.get(`${BASE}/figures/${slug}`);
    return res.data;
  },

  /** Fetch the figure image as a blob (auth handled by axios interceptor).
   *  Returns an object URL for use in <img src>. Caller should revoke when done. */
  async fetchFigureImage(slug: string): Promise<string | null> {
    try {
      const res = await api.get(`${BASE}/figures/${slug}/image`, { responseType: 'blob' });
      return URL.createObjectURL(res.data);
    } catch {
      return null;
    }
  },

  /** Fetch the figure's original uncropped source image (for re-cropping). */
  async fetchFigureSourceImage(slug: string): Promise<string | null> {
    try {
      const res = await api.get(`${BASE}/figures/${slug}/source-image`, { responseType: 'blob' });
      return URL.createObjectURL(res.data);
    } catch {
      return null;
    }
  },

  async createFigure(data: {
    slug?: string;
    label: string;
    figure_key: string;
    view_key: string;
    image: Blob;
    source?: Blob | null;
    sort_order?: number;
    is_active?: boolean;
  }): Promise<AnatomyFigure> {
    const fd = new FormData();
    fd.append('image', data.image);
    fd.append('label', data.label);
    fd.append('figure_key', data.figure_key);
    fd.append('view_key', data.view_key);
    if (data.slug) fd.append('slug', data.slug);
    if (data.source) fd.append('source', data.source);
    if (data.sort_order !== undefined) fd.append('sort_order', String(data.sort_order));
    if (data.is_active !== undefined) fd.append('is_active', String(data.is_active));
    const res = await api.post(`${BASE}/figures`, fd);
    return res.data;
  },

  async updateFigure(slug: string, data: Partial<{
    label: string;
    figure_key: string;
    view_key: string;
    sort_order: number;
    is_active: boolean;
    image: Blob;
    source: Blob | null;
    clear_source: boolean;
  }>): Promise<AnatomyFigure> {
    const fd = new FormData();
    if (data.image) fd.append('image', data.image);
    if (data.source) fd.append('source', data.source);
    if (data.clear_source) fd.append('clear_source', 'true');
    if (data.label !== undefined) fd.append('label', data.label);
    if (data.figure_key !== undefined) fd.append('figure_key', data.figure_key);
    if (data.view_key !== undefined) fd.append('view_key', data.view_key);
    if (data.sort_order !== undefined) fd.append('sort_order', String(data.sort_order));
    if (data.is_active !== undefined) fd.append('is_active', String(data.is_active));
    const res = await api.patch(`${BASE}/figures/${slug}`, fd);
    return res.data;
  },

  async deleteFigure(slug: string): Promise<void> {
    await api.delete(`${BASE}/figures/${slug}`);
  },
};
