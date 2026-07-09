/**
 * Cross-catalog relations graph — adapts the `/catalogs/{type}/{id}/relations`
 * response into the `ConceptGraphView` node/edge shapes.
 *
 * Extracted from TaxonomyManager's graph view (which was concept-only) so any
 * catalog type can render its polymorphic `concept_edges` subgraph. This is the
 * visual answer to "which organ does this biomarker affect? what treats this
 * disease?".
 */
import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ConceptGraphView,
  RELATION_COLORS,
  type ConceptGraphNode,
  type ConceptGraphEdgeData,
} from '../ui/ConceptGraphView';
import { LoadingState } from '../ui/LoadingState';
import { CatalogRelationsCards } from './CatalogRelationsCards';
import { getCatalogRelations } from '../../services/catalogService';
import type { CatalogRelationResponse } from '../../types/catalog';

interface CatalogRelationsGraphProps {
  catalogType: string;
  itemId: string;
  itemLabel?: string;
  /** Bump this to force a refetch (e.g. after relations are edited in the
   *  modal) without remounting and losing the depth/view selection. */
  refreshKey?: number;
}

export const CatalogRelationsGraph: React.FC<CatalogRelationsGraphProps> = ({
  catalogType,
  itemId,
  itemLabel,
  refreshKey,
}) => {
  const navigate = useNavigate();
  const [depth, setDepth] = useState(2);
  const [view, setView] = useState<'graph' | 'cards'>('graph');
  const [data, setData] = useState<CatalogRelationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | undefined>();

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getCatalogRelations(catalogType, itemId, { depth });
      setData(resp);
      setSelectedNodeId(resp.start?.id);
    } catch (e) {
      setError('Failed to load relations');
    } finally {
      setLoading(false);
    }
  }, [catalogType, itemId, depth]);

  React.useEffect(() => {
    load();
  }, [load, refreshKey]);

  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as ConceptGraphNode[], edges: [] as ConceptGraphEdgeData[] };
    const nodes: ConceptGraphNode[] = (data.nodes || []).map((n) => ({
      id: n.id,
      name: n.label || `${n.type}:${n.id.slice(0, 8)}`,
      primary_kind: n.kind || n.type,
      kinds: [n.kind || n.type],
      color: n.color || RELATION_COLORS[n.type] || '#6b7280',
    }));
    const edges: ConceptGraphEdgeData[] = (data.edges || []).map((e) => ({
      id: e.id,
      source: e.src.id,
      target: e.dst.id,
      relation: e.relation,
    }));
    return { nodes, edges };
  }, [data]);

  if (loading) return <LoadingState variant="section" message="Loading relations…" />;
  if (error) return <p className="text-sm text-red-500">{error}</p>;
  if (!data || nodes.length <= 1)
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400">
        No relations found for {itemLabel || 'this item'}.
      </p>
    );

  return (
    <div className="flex flex-col h-full gap-3">
      <div className="flex flex-wrap items-center gap-3 shrink-0">
        <label className="text-xs font-medium text-gray-600 dark:text-gray-400">
          Depth
        </label>
        <select
          value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}
          className="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm"
        >
          <option value={1}>1 hop</option>
          <option value={2}>2 hops</option>
          <option value={3}>3 hops</option>
        </select>
        <span className="text-xs text-gray-400">
          {nodes.length} nodes · {edges.length} edges
        </span>
        {/* Graph / Cards sub-tab toggle */}
        <div className="ml-auto flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
          <button
            onClick={() => setView('graph')}
            className={`px-3 py-1 text-xs font-medium ${
              view === 'graph'
                ? 'bg-blue-600 text-white'
                : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
          >
            Graph
          </button>
          <button
            onClick={() => setView('cards')}
            className={`px-3 py-1 text-xs font-medium ${
              view === 'cards'
                ? 'bg-blue-600 text-white'
                : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
          >
            Cards
          </button>
        </div>
      </div>

      {view === 'cards' ? (
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
          <CatalogRelationsCards
            data={data}
            onSelectEndpoint={(type, id) =>
              navigate(`/catalogs?type=${type}&item=${id}`)
            }
          />
        </div>
      ) : (
        <div className="flex-1 relative min-h-[400px] rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <ConceptGraphView
            nodes={nodes}
            edges={edges}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNodeId}
          />
        </div>
      )}
    </div>
  );
};
