/**
 * Per-type write-target dispatcher (taxonomy/catalog merge plan §4.3, AD-1 r2).
 *
 * The catalog workspace's generic ``createCatalogItem`` / ``updateCatalogItem``
 * / ``deleteCatalogItem`` hit ``/catalogs/{type}``. Concepts are **read-only**
 * through that meta-layer (the backend returns 405 — the safety net). All
 * concept writes route through the ``/concepts`` REST surface instead, which
 * delegates to ``ConceptService`` (kind-sync, retire, audit, RBAC — the single
 * write authority).
 *
 * This dispatcher lets the workspace stay generic for *rendering* while routing
 * writes to the correct endpoint per type. Adding another read-only-via-meta-
 * layer type = one entry here.
 */
import type { CatalogItem } from '../../types/catalog';
import {
  createConcept,
  updateConcept,
  deleteConcept,
  restoreConcept,
} from '../../services/conceptService';

export interface WriteTarget {
  create: (data: Record<string, unknown>) => Promise<Record<string, unknown>>;
  update: (id: string, data: Record<string, unknown>) => Promise<Record<string, unknown>>;
  remove: (id: string) => Promise<void>;
  restore?: (id: string) => Promise<Record<string, unknown>>;
}

const conceptTarget: WriteTarget = {
  create: async (data) => createConcept(data as any) as any,
  update: async (id, data) => updateConcept(id, data as any) as any,
  remove: async (id) => deleteConcept(id),
  restore: async (id) => restoreConcept(id) as any,
};

/**
 * Resolve the write target for a catalog type. Returns ``null`` for types
 * that write through the generic ``/catalogs/{type}`` endpoints (the default).
 */
export function getWriteTarget(type: string): WriteTarget | null {
  switch (type) {
    case 'concept':
      return conceptTarget;
    default:
      return null;
  }
}

/**
 * Normalize a catalog draft (the ``editing`` object) into the payload shape the
 * write target expects. For concepts this maps the flat catalog-item fields to
 * ``ConceptCreateInput`` / ``ConceptUpdateInput``.
 */
export function buildWritePayload(
  type: string,
  draft: CatalogItem,
  mode: 'create' | 'edit',
): Record<string, unknown> {
  if (type === 'concept') {
    const payload: Record<string, unknown> = {
      name: draft.name ?? '',
      kinds: (draft.kinds as string[]) ?? [],
      parent_id: draft.parent_id ?? null,
      description: draft.description ?? null,
      coding_system: draft.coding_system ?? null,
      code: draft.code ?? null,
      aliases: draft.aliases ?? [],
      icon: draft.icon ?? null,
      color: draft.color ?? null,
      display_order: draft.display_order ?? 0,
    };
    if (mode === 'create') {
      payload.slug = draft.slug ?? '';
      payload.tenant_scoped = draft.tenant_scoped ?? false;
    } else {
      if (draft.status) payload.status = draft.status;
      if (draft.meta_data !== undefined) payload.meta_data = draft.meta_data;
    }
    return payload;
  }
  // Default: pass the draft as-is (the generic catalog endpoint handles it).
  return { ...draft };
}
