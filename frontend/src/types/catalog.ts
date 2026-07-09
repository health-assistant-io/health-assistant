/**
 * Types for the unified Catalog meta-layer (`/catalogs` endpoints).
 *
 * Mirrors the backend shapes from `app/api/v1/endpoints/catalogs.py` and
 * `app/catalogs/registrations.py`. The workspace is registry-driven: the left
 * rail renders the catalog types returned by `GET /catalogs`, and each type's
 * items/relations are fetched on selection.
 */

export type CatalogType =
  | 'biomarker'
  | 'medication'
  | 'allergy'
  | 'anatomy'
  | 'vaccine'
  | 'concept';

export interface CatalogUiMeta {
  label_key: string;
  icon: string;
  color: string;
  admin_route: string;
}

export interface CatalogTypeMeta {
  type: CatalogType;
  ui: CatalogUiMeta;
  has_concept_link: boolean;
  edge_endpoint_type: string;
  search_columns: string[];
}

export interface CatalogTypeListResponse {
  types: CatalogTypeMeta[];
}

export interface CatalogListResponse {
  items: CatalogItem[];
  total: number;
}

/**
 * The ownership/scope tier of a catalog item (Phase A access-control model).
 * - `system` — canonical reference (SYSTEM_ADMIN only)
 * - `tenant` — shared across the tenant (ADMIN/MANAGER manage)
 * - `user`   — personal entry by `created_by` (creator + ADMIN edit)
 */
export type CatalogScope = 'system' | 'tenant' | 'user';

/**
 * One catalog item. Carries the scope/ownership + audit-mixin fields surfaced
 * in Phase A/B (scope, tenant_id, created_by, updated_at). Catalog-specific
 * extra fields (slug, code, aliases, …) remain accessible via the index
 * signature.
 */
export interface CatalogItem extends Record<string, unknown> {
  id?: string;
  name?: string;
  slug?: string;
  scope?: CatalogScope;
  tenant_id?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** Taxonomy-class concept (anatomy/biomarker/… catalogs). */
  class_concept_id?: string | null;
  class_concept_slug?: string | null;
  class_concept_name?: string | null;
  /** Annotated when the list is fetched with include=relations. */
  relation_count?: number;
  relation_breakdown?: Record<string, number>;
}

/** One immutable entry in a catalog item's audit trail (Phase B). */
export interface CatalogAuditEntry {
  id: string;
  tenant_id?: string | null;
  user_id?: string | null;
  user_email: string;
  catalog_type: string;
  item_id: string;
  item_name: string;
  operation: 'create' | 'update' | 'delete' | 'promote' | 'demote';
  from_scope?: CatalogScope | null;
  to_scope?: CatalogScope | null;
  details?: Record<string, unknown> | null;
  created_at: string;
}

export interface CatalogAuditHistoryResponse {
  items: CatalogAuditEntry[];
}

export interface CatalogSearchHit {
  type: string;
  id: string;
  label: string;
}

/**
 * A catalog item picked via {@link CatalogItemPicker}. Carries enough to
 * identify + render it (`type` is the catalog slug, e.g. 'medication') plus an
 * optional `relation` when the picker is used in relation-binding mode.
 */
export interface CatalogSelection {
  type: string;
  id: string;
  label: string;
  relation?: string;
}

export interface CatalogSearchResponse {
  results: CatalogSearchHit[];
}

/**
 * Rich metadata for one ``ConceptRelationType`` (from
 * ``GET /catalogs/relation-types``). Powers the relation-picker's icon + the
 * "when to use this" info affordance.
 */
export interface RelationTypeMeta {
  value: string;
  label: string;
  group: string;
  description: string;
  icon: { type: 'lucide' | 'custom_svg'; value: string };
}

export interface RelationTypeListResponse {
  items: RelationTypeMeta[];
}

export interface CatalogRelationEndpoint {
  type: string;
  id: string;
  label: string;
  icon?: { type: string; value: string } | null;
  color?: string | null;
  kind?: string | null;
}

export interface CatalogRelationEdge {
  id: string;
  src: { type: string; id: string };
  dst: { type: string; id: string };
  relation: string;
  status: string;
}

export interface CatalogRelationResponse {
  start: CatalogRelationEndpoint;
  nodes: CatalogRelationEndpoint[];
  edges: CatalogRelationEdge[];
}
