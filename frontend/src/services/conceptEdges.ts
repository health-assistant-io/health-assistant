/**
 * Concept-edges (knowledge-graph links) service.
 *
 * Thin client over the backend `/concept-edges/*` endpoints. Used by:
 *  - {@link useLinkSchema} — discovery: which (src_type, dst_type) pairs the
 *    graph accepts and which relations are valid for each. Pure metadata, no
 *    DB hit on the server.
 *  - {@link createLinksFor} — the HITL confirm-flow helper. After a primary
 *    entity (e.g. a medication catalog entry) is committed, the form loops
 *    the user-edited `links[]` and POSTs each one. Best-effort: per-link
 *    failures don't abort the loop; the caller gets a per-link result array
 *    it can surface in the audit JSONB.
 *  - {@link CatalogRelationsEditor} (existing) — for editing outgoing edges
 *    of an already-persisted catalog item. Uses `createEdge`/`deleteEdge`
 *    from `conceptService` directly.
 *
 * Backend contract: see `app/ai/tools/propose_link.py` (`LINK_SCHEMA` is the
 * single source of truth) and `app/api/v1/endpoints/concepts.py`
 * (`GET /concept-edges/schema`, `POST /concept-edges`).
 */
import api from '../api/axios';
import { createEdge } from './conceptService';
import type { CatalogSelection } from '../types/catalog';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Shape returned by `GET /concept-edges/schema` with no arguments — the
 *  full matrix as a flat list. Mirrors the backend `serialize_full_schema`. */
export interface LinkSchemaRow {
  src_type: string;
  dst_type: string;
  relations: string[];
}

/** Shape returned by `GET /concept-edges/schema?src_type=X` — keyed by
 *  destination type. */
export type LinkSchemaForSource = Record<string, string[]>;

/** Shape returned by `GET /concept-edges/schema?src_type=X&dst_type=Y` —
 *  just the flat list of valid relations. */
export interface LinkSchemaForPair {
  relations: string[];
}

/** Per-link outcome from {@link createLinksFor}. The `ok` flag is the only
 *  field the form needs to render success/failure; the rest is for audit. */
export interface LinkCreateResult {
  ok: boolean;
  /** The destination as the user picked it (echoed back for the audit trail). */
  dst: { type: string; id: string; label: string };
  relation: string;
  /** Populated when `ok === true`. */
  edge_id?: string;
  /** Populated when `ok === false`. */
  error?: string;
}

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

/**
 * Fetch the link-schema matrix from the backend.
 *
 * The matrix is pure metadata (no DB hit on the server), so callers can safely
 * cache it for the app lifetime (see {@link useLinkSchema}). Pass `srcType`
 * to filter to relations FROM that catalog/endpoint type; pass both `srcType`
 * and `dstType` for a specific pair.
 *
 * The response shape varies by filter:
 *  - neither     → `LinkSchemaRow[]`
 *  - srcType     → `LinkSchemaForSource` (object keyed by dst_type)
 *  - srcType+dst → `LinkSchemaForPair` ({ relations: [...] })
 */
export async function getLinkSchema(): Promise<LinkSchemaRow[]>;
export async function getLinkSchema(srcType: string): Promise<LinkSchemaForSource>;
export async function getLinkSchema(srcType: string, dstType: string): Promise<LinkSchemaForPair>;
export async function getLinkSchema(
  srcType?: string,
  dstType?: string,
): Promise<LinkSchemaRow[] | LinkSchemaForSource | LinkSchemaForPair> {
  const params = new URLSearchParams();
  if (srcType) params.set('src_type', srcType);
  if (dstType) params.set('dst_type', dstType);
  const qs = params.toString();
  const response = await api.get(`/concept-edges/schema${qs ? `?${qs}` : ''}`);
  return response.data;
}

// ---------------------------------------------------------------------------
// Write helper — used by the HITL confirm flow after a primary create
// ---------------------------------------------------------------------------

/** Input shape for {@link createLinksFor}. The destination fields mirror the
 *  {@link CatalogSelection} the picker emits; pass `selection.id` / `.type` /
 *  `.label` / `.relation` straight through. */
export interface LinkInput {
  dst_type: string;
  dst_id: string;
  dst_label?: string;
  relation: string;
  properties?: Record<string, unknown>;
}

/**
 * Create a batch of outgoing edges from one source to multiple destinations.
 *
 * Used by the HITL form's `onSubmit` handler: after the primary entity is
 * committed (returning `srcId`), the form loops the user-edited `links[]`
 * and calls this. Best-effort — a failure on one link (e.g. duplicate edge,
 * RBAC denial, missing destination) does NOT abort the loop; the result
 * array carries the per-link outcome so the caller can surface partial
 * failures in the audit JSONB and on the card.
 *
 * The `tenant_scoped` flag mirrors `ConceptEdgeCreate.tenant_scoped` — true =
 * tenant-scoped edge (default), false = global (requires SYSTEM_ADMIN on the
 * server; non-admins get 403 on the failing link, the loop continues).
 */
export async function createLinksFor(
  srcType: string,
  srcId: string,
  links: LinkInput[],
  opts: { tenant_scoped?: boolean; source?: string; status?: string } = {},
): Promise<LinkCreateResult[]> {
  const { tenant_scoped = true, source = 'ai', status = 'approved' } = opts;
  const results: LinkCreateResult[] = [];
  for (const link of links) {
    try {
      const edge = await createEdge({
        src_type: srcType,
        src_id: srcId,
        dst_type: link.dst_type,
        dst_id: link.dst_id,
        relation: link.relation,
        properties: link.properties,
        source,
        status,
        tenant_scoped,
      });
      results.push({
        ok: true,
        dst: { type: link.dst_type, id: link.dst_id, label: link.dst_label ?? '' },
        relation: link.relation,
        edge_id: edge.id,
      });
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : typeof err === 'string'
            ? err
            : 'unknown error';
      results.push({
        ok: false,
        dst: { type: link.dst_type, id: link.dst_id, label: link.dst_label ?? '' },
        relation: link.relation,
        error: message,
      });
    }
  }
  return results;
}

/** Convert the picker's `CatalogSelection[]` to `LinkInput[]` for
 *  {@link createLinksFor}. One-liner kept as a named export so the conversion
 *  is testable and the form doesn't duplicate the mapping. */
export function selectionsToLinkInputs(
  selections: CatalogSelection[],
): LinkInput[] {
  return selections
    .filter((s) => Boolean(s.relation))
    .map((s) => ({
      dst_type: s.type,
      dst_id: s.id,
      dst_label: s.label,
      relation: s.relation as string,
    }));
}
