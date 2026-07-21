/**
 * Inline relations editor for the catalog item form (Phase D follow-up).
 *
 * Lets an editor manage the outgoing `concept_edges` of the item being edited:
 * add a relation (pick a relation type + search any catalog for the target)
 * and remove existing ones. Cross-catalog: a biomarker can AFFECT an anatomy
 * item, MONITOR a disease concept, be a MEMBER_OF a panel, etc.
 *
 * Now a thin consumer of {@link CatalogItemPicker} (relation mode): the picker
 * owns the search/browse/chip UI; this component owns only the edge ↔ selection
 * mapping and the polymorphic-edge persistence (`POST/DELETE /concept-edges`).
 * Only shown in edit mode (the item must already have an id).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link2 } from 'lucide-react';
import { getCatalogRelations } from '../../services/catalogService';
import { createEdge, deleteEdge } from '../../services/conceptService';
import { CatalogItemPicker, selectionKey } from './CatalogItemPicker';
import type {
  CatalogRelationEdge,
  CatalogRelationEndpoint,
  CatalogSelection,
  CatalogTypeMeta,
} from '../../types/catalog';

/** Catalog-type → polymorphic edge-endpoint type. Identity for most, except
 *  vaccine → immunization. Kept in sync with the backend EdgeEndpointType. */
const CATALOG_TYPE_TO_ENDPOINT: Record<string, string> = {
  biomarker: 'biomarker',
  medication: 'medication',
  allergy: 'allergy',
  vaccine: 'immunization',
  anatomy: 'anatomy',
  concept: 'concept',
};

interface CatalogRelationsEditorProps {
  typeMeta: CatalogTypeMeta;
  itemId: string;
}

export const CatalogRelationsEditor: React.FC<CatalogRelationsEditorProps> = ({
  typeMeta,
  itemId,
}) => {
  const { t } = useTranslation();
  const [edges, setEdges] = useState<CatalogRelationEdge[]>([]);
  const [nodeIndex, setNodeIndex] = useState<Record<string, CatalogRelationEndpoint>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const srcEndpoint = typeMeta.edge_endpoint_type;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getCatalogRelations(typeMeta.type, itemId, { depth: 1 });
      const idx: Record<string, CatalogRelationEndpoint> = {};
      for (const n of resp.nodes || []) idx[n.id] = n;
      setNodeIndex(idx);
      // Outgoing edges only (src === this item).
      setEdges((resp.edges || []).filter((e) => e.src.id === itemId));
    } catch {
      setEdges([]);
    } finally {
      setLoading(false);
    }
  }, [typeMeta.type, itemId]);

  useEffect(() => {
    load();
  }, [load]);

  /** Current persisted edges projected into picker selections. */
  const selections = useMemo<CatalogSelection[]>(() => {
    return edges.map((e) => {
      const node = nodeIndex[e.dst.id];
      return {
        type: node?.type ?? e.dst.type,
        id: e.dst.id,
        label: node?.label ?? `${e.dst.id.slice(0, 8)}`,
        relation: e.relation,
      };
    });
  }, [edges, nodeIndex]);

  /** Diff the picker's new selection against the persisted edges, then
   *  create/delete concept_edges to reconcile. Persists immediately (matching
   *  the original editor's UX). */
  const handleChange = useCallback(
    async (next: CatalogSelection[]) => {
      setError(null);
      setBusy(true);
      const curKeys = new Set(selections.map(selectionKey));
      const nextKeys = new Set(next.map(selectionKey));

      // Removed relations → delete their edges.
      for (const sel of selections) {
        if (nextKeys.has(selectionKey(sel))) continue;
        const edge = edges.find(
          (e) => e.dst.id === sel.id && e.relation === sel.relation,
        );
        if (!edge) continue;
        try {
          await deleteEdge(edge.id);
        } catch {
          setError(t('catalogs.edge_remove_error', 'Could not remove relation.'));
        }
      }

      // Added relations → create edges.
      for (const sel of next) {
        if (curKeys.has(selectionKey(sel))) continue;
        const dstEndpoint = CATALOG_TYPE_TO_ENDPOINT[sel.type] ?? sel.type;
        try {
          await createEdge({
            src_type: srcEndpoint,
            src_id: itemId,
            dst_type: dstEndpoint,
            dst_id: sel.id,
            relation: sel.relation ?? 'AFFECTS',
            source: 'manual',
            status: 'approved',
            tenant_scoped: true,
          });
        } catch {
          setError(t('catalogs.edge_add_error', 'Could not add relation.'));
        }
      }

      await load(); // reconcile persisted state
      setBusy(false);
    },
    [selections, edges, srcEndpoint, itemId, load, t],
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-500">
        <Link2 className="w-3.5 h-3.5" />
        {t('catalogs.relations_editor_title', 'Relations')}
        {busy && <span className="font-normal text-gray-400">…</span>}
      </div>

      {loading ? (
        <p className="text-xs text-gray-400">{t('common.loading', 'Loading…')}</p>
      ) : (
        <CatalogItemPicker
          mode="multi"
          value={selections}
          onChange={handleChange}
          relationPicker={{}}
          displayMode="cards"
          placeholder={t('catalogs.edge_search_placeholder', 'Search any catalog for the target…')}
        />
      )}

      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
};
