/**
 * Whole cross-catalog ontology graph view.
 *
 * Calls the rootless ``GET /catalogs/graph`` endpoint (one server-filtered
 * call) and renders the full polymorphic graph — concepts, biomarkers,
 * medications, anatomy, allergies, vaccines — via ``<ConceptGraphView>``.
 *
 * Two filter rows:
 * 1. **Catalog-type chips** (concept, biomarker, medication, ...) — toggles
 *    which catalog types are included. Server-side filter (reloads on change).
 * 2. **Concept-kind sub-chips** (disease, symptom, ...) — appears only when
 *    "concept" is active. Also server-side.
 *
 * Additional client-side controls: depth BFS, dim chips, anatomy overlay
 * (subsumed by the catalog-type chips).
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ConceptGraphView,
  type ConceptGraphNode,
  type ConceptGraphEdgeData,
} from '../ui/ConceptGraphView';
import { getCatalogGraph } from '../../services/catalogService';
import {
  CONCEPT_KIND_LABELS,
  KIND_COLORS,
  CATALOG_TYPE_COLORS,
  CATALOG_TYPE_LABELS,
  type ConceptKind,
} from '../../types/concept';

const ALL_CATALOG_TYPES = Object.keys(CATALOG_TYPE_LABELS);

interface CatalogOntologyGraphProps {
  /** Called when a node is double-clicked (focus). */
  onFocusNode?: (conceptId: string) => void;
  /** Called when a node is single-clicked (select). */
  onSelectNode?: (conceptId: string) => void;
  /** Bump to force a refetch without remounting. */
  refreshKey?: number;
}

export const CatalogOntologyGraph: React.FC<CatalogOntologyGraphProps> = ({
  onFocusNode,
  onSelectNode,
  refreshKey = 0,
}) => {
  const { t } = useTranslation();
  const [rawNodes, setRawNodes] = useState<ConceptGraphNode[]>([]);
  const [rawEdges, setRawEdges] = useState<ConceptGraphEdgeData[]>([]);
  const [loading, setLoading] = useState(true);
  const [truncated, setTruncated] = useState(false);

  // Server-side filters
  const [activeTypes, setActiveTypes] = useState<Set<string>>(new Set());
  const [activeKinds, setActiveKinds] = useState<Set<ConceptKind>>(new Set());

  // Client-side filters
  const [selectedNode, setSelectedNode] = useState<string | undefined>();
  const [depth, setDepth] = useState(0);
  const [hiddenKinds, setHiddenKinds] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const typesParam = activeTypes.size > 0
        ? [...activeTypes].join(',')
        : undefined;
      const kindParam = activeKinds.size > 0
        ? [...activeKinds].join(',')
        : undefined;
      const resp = await getCatalogGraph({
        types: typesParam,
        kind: kindParam,
        limit: 10000,
      });
      setRawNodes(
        resp.nodes.map((n) => {
          const kindOrType = n.kind || n.type;
          return {
            id: n.id,
            name: n.label || `${n.type}:${n.id.slice(0, 8)}`,
            primary_kind: kindOrType,
            kinds: [kindOrType],
            color: n.color
              || KIND_COLORS[kindOrType as ConceptKind]
              || CATALOG_TYPE_COLORS[kindOrType]
              || '#6b7280',
          };
        }),
      );
      setRawEdges(
        resp.edges.map((e) => ({
          id: e.id,
          source: e.src.id,
          target: e.dst.id,
          relation: e.relation,
        })),
      );
      setTruncated(resp.truncated);
    } catch {
      setRawNodes([]);
      setRawEdges([]);
    } finally {
      setLoading(false);
    }
  }, [activeTypes, activeKinds]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  // Client-side BFS depth filter from the selected node.
  const displayedGraph = useMemo(() => {
    if (depth === 0 || !selectedNode) {
      return { nodes: rawNodes, edges: rawEdges };
    }
    const adj = new Map<string, string[]>();
    for (const e of rawEdges) {
      if (!adj.has(e.source)) adj.set(e.source, []);
      if (!adj.has(e.target)) adj.set(e.target, []);
      adj.get(e.source)!.push(e.target);
      adj.get(e.target)!.push(e.source);
    }
    const visited = new Set<string>([selectedNode]);
    let frontier = [selectedNode];
    for (let d = 0; d < depth && frontier.length > 0; d++) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const nbr of adj.get(id) ?? []) {
          if (!visited.has(nbr)) {
            visited.add(nbr);
            next.push(nbr);
          }
        }
      }
      frontier = next;
    }
    return {
      nodes: rawNodes.filter((n) => visited.has(n.id)),
      edges: rawEdges.filter(
        (e) => visited.has(e.source) && visited.has(e.target),
      ),
    };
  }, [rawNodes, rawEdges, depth, selectedNode]);

  const toggleType = (type: string) => {
    setActiveTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const toggleKind = (kind: ConceptKind) => {
    setActiveKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const toggleHiddenKind = (kind: string) => {
    setHiddenKinds((prev) =>
      prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind],
    );
  };

  const allKinds = Object.keys(CONCEPT_KIND_LABELS) as ConceptKind[];
  const conceptActive = activeTypes.size === 0 || activeTypes.has('concept');

  return (
    <div className="flex flex-col h-full min-h-[500px] gap-2">
      {/* Controls bar */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2">
        {/* Catalog-type filter chips (server-side) */}
        <span className="text-xs font-medium text-gray-500 dark:text-gray-400 mr-1">
          {t('catalogs.graph_types', 'Types')}:
        </span>
        <div className="flex flex-wrap gap-1">
          {ALL_CATALOG_TYPES.map((type) => {
            const active = activeTypes.size === 0 || activeTypes.has(type);
            return (
              <button
                key={type}
                onClick={() => toggleType(type)}
                className={`px-2 py-0.5 text-[11px] font-bold rounded-full border transition-all ${
                  active
                    ? 'text-white border-transparent'
                    : 'border-gray-200 dark:border-gray-600 text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 opacity-40'
                }`}
                style={active ? { backgroundColor: CATALOG_TYPE_COLORS[type] || '#6b7280' } : undefined}
              >
                {CATALOG_TYPE_LABELS[type]}
              </button>
            );
          })}
        </div>

        {/* Concept-kind sub-chips (only when concepts are active) */}
        {conceptActive && (
          <>
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400 ml-2">
              {t('catalogs.graph_kinds', 'Kinds')}:
            </span>
            <div className="flex flex-wrap gap-1">
              {allKinds.map((kind) => {
                const active = activeKinds.has(kind);
                return (
                  <button
                    key={kind}
                    onClick={() => toggleKind(kind)}
                    className={`px-1.5 py-0.5 text-[10px] font-bold rounded-full border transition-all ${
                      active
                        ? 'text-white border-transparent'
                        : 'border-gray-200 dark:border-gray-600 text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                    }`}
                    style={active ? { backgroundColor: KIND_COLORS[kind] } : undefined}
                  >
                    {CONCEPT_KIND_LABELS[kind]}
                  </button>
                );
              })}
            </div>
          </>
        )}

        {/* Depth chips (client-side BFS) */}
        {selectedNode && (
          <div className="flex items-center gap-1 ml-2">
            <span className="text-[10px] text-gray-400">
              {t('catalogs.graph_depth', 'Depth')}:
            </span>
            {[0, 1, 2, 3, 4].map((d) => (
              <button
                key={d}
                onClick={() => setDepth(d)}
                className={`w-6 h-6 text-[11px] rounded-md font-medium ${
                  depth === d
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'
                }`}
              >
                {d === 0 ? '∞' : d}
              </button>
            ))}
          </div>
        )}

        <div className="flex-1" />

        {truncated && (
          <span className="text-[10px] text-amber-500">
            {t('catalogs.graph_truncated', 'Capped at 10k edges — narrow with filters')}
          </span>
        )}
        <span className="text-[10px] text-gray-400">
          {displayedGraph.nodes.length} {t('catalogs.graph_nodes', 'nodes')} ·{' '}
          {displayedGraph.edges.length} {t('catalogs.graph_edges', 'edges')}
        </span>
      </div>

      {/* Graph canvas */}
      <div className="flex-1 min-h-0 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden bg-gray-50 dark:bg-gray-900">
        {loading ? (
          <div className="flex items-center justify-center h-full text-sm text-gray-400">
            {t('common.loading', 'Loading…')}
          </div>
        ) : displayedGraph.nodes.length === 0 ? (
          <div className="flex items-center justify-center h-full text-sm text-gray-400">
            {t('catalogs.graph_empty', 'No graph data. Add relations between catalog items to see them here.')}
          </div>
        ) : (
          <ConceptGraphView
            nodes={displayedGraph.nodes}
            edges={displayedGraph.edges}
            selectedNodeId={selectedNode}
            hiddenKinds={hiddenKinds}
            onSelectNode={(id) => {
              setSelectedNode(id);
              onSelectNode?.(id);
            }}
            onFocusNode={(id) => {
              setSelectedNode(id);
              onFocusNode?.(id);
            }}
          />
        )}
      </div>

      {/* Hidden-kind dimming chips (client-side) */}
      <div className="flex flex-wrap items-center gap-1">
        <span className="text-[10px] text-gray-400">
          {t('catalogs.graph_dim', 'Dim')}:
        </span>
        {allKinds.map((kind) => (
          <button
            key={kind}
            onClick={() => toggleHiddenKind(kind)}
            className={`px-1.5 py-0.5 text-[9px] rounded border ${
              hiddenKinds.includes(kind)
                ? 'bg-gray-400 text-white border-transparent'
                : 'border-gray-200 dark:border-gray-600 text-gray-300 hover:bg-gray-50'
            }`}
          >
            {CONCEPT_KIND_LABELS[kind]}
          </button>
        ))}
      </div>
    </div>
  );
};
