/**
 * Service wrapper for the unified Catalog meta-layer (`/catalogs` endpoints).
 *
 * The `/catalogs` surface is a thin read-mostly + admin-write layer that
 * dispatches by `type` to the registered domain service. It never duplicates
 * domain logic — it calls the domain services via the registry. This service
 * follows the named-function export convention (matching `conceptService`).
 */
import api from '../api/axios';
import type {
  CatalogTypeListResponse,
  CatalogListResponse,
  CatalogSearchResponse,
  CatalogRelationResponse,
  CatalogAuditHistoryResponse,
  CatalogType,
  RelationTypeListResponse,
  RelationTypeMeta,
} from '../types/catalog';

/**
 * List every registered catalog type + its UI metadata (the left-rail data).
 * No DB hit on the backend — pure registry read.
 */
export async function listCatalogTypes(): Promise<CatalogTypeListResponse> {
  const { data } = await api.get<CatalogTypeListResponse>('/catalogs');
  return data;
}

/**
 * List items of one catalog type (tenant-scoped: global + caller's tenant).
 * `scope` narrows to a single tier (system | tenant | user).
 */
export async function listCatalogItems(
  type: CatalogType | string,
  params?: {
    search?: string;
    scope?: string;
    include?: string;
    kind?: string;
    class?: string;
    limit?: number;
    offset?: number;
  },
): Promise<CatalogListResponse> {
  const { data } = await api.get<CatalogListResponse>(`/catalogs/${type}`, {
    params,
  });
  return data;
}

/**
 * Get a single catalog item by id.
 */
export async function getCatalogItem(
  type: CatalogType | string,
  itemId: string,
): Promise<Record<string, unknown>> {
  const { data } = await api.get<Record<string, unknown>>(
    `/catalogs/${type}/${itemId}`,
  );
  return data;
}

/**
 * Create a catalog item (ADMIN/MANAGER for tenant rows; SYSTEM_ADMIN for global).
 */
export async function createCatalogItem(
  type: CatalogType | string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const { data } = await api.post<Record<string, unknown>>(
    `/catalogs/${type}`,
    payload,
  );
  return data;
}

/**
 * Update a catalog item (global rows require SYSTEM_ADMIN).
 */
export async function updateCatalogItem(
  type: CatalogType | string,
  itemId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const { data } = await api.put<Record<string, unknown>>(
    `/catalogs/${type}/${itemId}`,
    payload,
  );
  return data;
}

/**
 * Delete a catalog item (global rows require SYSTEM_ADMIN).
 */
export async function deleteCatalogItem(
  type: CatalogType | string,
  itemId: string,
): Promise<void> {
  await api.delete(`/catalogs/${type}/${itemId}`);
}

/**
 * Unified cross-catalog search. Returns ranked hits across all (or selected)
 * catalog types. Powers the global search box.
 */
export async function searchCatalogs(
  q: string,
  params?: { types?: string; limit?: number },
): Promise<CatalogSearchResponse> {
  const { data } = await api.get<CatalogSearchResponse>('/catalogs/search', {
    params: { q, ...params },
  });
  return data;
}

/**
 * Cross-catalog graph traversal from a start node. Returns the polymorphic
 * `concept_edges` subgraph reachable within `depth` hops.
 */
export async function getCatalogRelations(
  type: CatalogType | string,
  itemId: string,
  params?: {
    depth?: number;
    relation?: string;
    include_proposed?: boolean;
  },
): Promise<CatalogRelationResponse> {
  const { data } = await api.get<CatalogRelationResponse>(
    `/catalogs/${type}/${itemId}/relations`,
    { params },
  );
  return data;
}

/**
 * The audit trail for one catalog item (newest-first), tenant-scoped.
 * Powers the Phase B "History" modal.
 */
export async function getCatalogItemHistory(
  type: CatalogType | string,
  itemId: string,
): Promise<CatalogAuditHistoryResponse> {
  const { data } = await api.get<CatalogAuditHistoryResponse>(
    `/catalogs/${type}/${itemId}/history`,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Relation-type reference metadata — cached after the first fetch (mirrors the
// document-categories cache in conceptService). The backend
// `app/catalogs/relation_types.py` registry is the single source of truth for
// the label / description / icon / group of each ConceptRelationType.
// ---------------------------------------------------------------------------

let _relationTypes: RelationTypeMeta[] = [];
let _relationTypeMap = new Map<string, RelationTypeMeta>();
let _relationTypesPromise: Promise<RelationTypeMeta[]> | null = null;

/** Fetch + cache the relation-type metadata. Resolves once per session. */
export async function loadRelationTypes(): Promise<RelationTypeMeta[]> {
  if (_relationTypesPromise) return _relationTypesPromise;
  _relationTypesPromise = (async () => {
    try {
      const { data } = await api.get<RelationTypeListResponse>(
        '/catalogs/relation-types',
      );
      _relationTypes = data.items ?? [];
      _relationTypeMap = new Map(_relationTypes.map((r) => [r.value, r]));
    } catch {
      // Degrade gracefully — callers fall back to the bundled defaults.
      _relationTypes = [];
      _relationTypeMap = new Map();
    }
    return _relationTypes;
  })();
  return _relationTypesPromise;
}

/** Synchronous read of the cached relation-type metadata (empty until loaded). */
export function getRelationTypes(): RelationTypeMeta[] {
  return _relationTypes;
}

/** Look up one relation's metadata by its wire value (e.g. ``AFFECTS``). */
export function getRelationType(value: string): RelationTypeMeta | undefined {
  return _relationTypeMap.get(value);
}

/** Force a re-fetch on next `loadRelationTypes()` (e.g. after a backend update). */
export function invalidateRelationTypes(): void {
  _relationTypesPromise = null;
  _relationTypes = [];
  _relationTypeMap = new Map();
}
