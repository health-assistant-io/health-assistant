/**
 * Whole-ontology graph view for the concept catalog (Phase 5, Option B).
 *
 * Calls the rootless ``GET /catalogs/concept/graph`` endpoint (one server-
 * filtered call) and renders the full concept graph via ``<ConceptGraphView>``.
 * Supports kind filtering (reloads from server), depth limiting (client-side
 * BFS from selected node), anatomy overlay toggle, and PNG export.
 *
 * This replaces TaxonomyManager's client-side two-call assembly with a single
 * server-side endpoint that filters both concepts and edges by kind — keeping
 * the payload proportional to the filtered domain, not the full ontology.
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ConceptGraphView,
  type ConceptGraphNode,
  type ConceptGraphEdgeData,
} from '../ui/ConceptGraphView';
import { getConceptGraph } from '../../services/catalogService';
import {
  CONCEPT_KIND_LABELS,
  KIND_COLORS,
  type ConceptKind,
} from '../../types/concept';

interface ConceptOntologyGraphProps {
  /** Called when a node is double-clicked (focus). */
  onFocusNode?: (conceptId: string) => void;
  /** Called when a node is single-clicked (select). */
  onSelectNode?: (conceptId: string) => void;
  /** Bump to force a refetch without remounting. */
  refreshKey?: number;
}

export const ConceptOntologyGraph: React.FC<ConceptOntologyGraphProps> = ({
  onFocusNode,
  onSelectNode,
  refreshKey = 0,
}) => {
  const { t } = useTranslation();
  const [rawNodes, setRawNodes] = useState<ConceptGraphNode[]>([]);
  const [rawEdges, setRawEdges] = useState<ConceptGraphEdgeData[]>([]);
  const [loading, setLoading] = useState(true);
  const [truncated, setTruncated] = useState(false);

  // Interactive state
  const [activeKinds, setActiveKinds] = useState<Set<ConceptKind>>(new Set());
  const [includeAnatomy, setIncludeAnatomy] = useState(false);
  const [selectedNode, setSelectedNode] = useState<string | undefined>();
  const [depth, setDepth] = useState(0); // 0 = unlimited
  const [hiddenKinds, setHiddenKinds] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const kindParam = activeKinds.size > 0
        ? [...activeKinds].join(',')
        : undefined;
      const resp = await getConceptGraph({
        kind: kindParam,
        include_anatomy: includeAnatomy,
        limit: 500,
      });
      setRawNodes(
        resp.nodes.map((n) => ({
          id: n.id,
          name: n.label || `${n.type}:${n.id.slice(0, 8)}`,
          primary_kind: n.kind || n.type,
          kinds: [n.kind || n.type],
          color: n.color || KIND_COLORS[(n.kind || n.type) as ConceptKind] || '#6b7280',
        })),
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
  }, [activeKinds, includeAnatomy]);

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
    const visSet = visited;
    return {
      nodes: rawNodes.filter((n) => visSet.has(n.id)),
      edges: rawEdges.filter(
        (e) => visSet.has(e.source) && visSet.has(e.target),
      ),
    };
  }, [rawNodes, rawEdges, depth, selectedNode]);

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

  return (
    <div className="flex flex-col h-full min-h-[500px] gap-2">
      {/* Controls bar */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2">
        {/* Kind filter chips (server-side filter — reloads on change) */}
        <span className="text-xs font-medium text-gray-500 dark:text-gray-400 mr-1">
          {t('catalogs.graph_filter_kinds', 'Filter')}:
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

        {/* Anatomy overlay toggle */}
        <button
          onClick={() => setIncludeAnatomy((v) => !v)}
          className={`px-2 py-0.5 text-[11px] font-medium rounded-md border transition-colors ${
            includeAnatomy
              ? 'bg-green-600 text-white border-transparent'
              : 'border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
          }`}
        >
          {t('catalogs.graph_anatomy', 'Anatomy')}
        </button>

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

        {/* Truncation indicator */}
        {truncated && (
          <span className="text-[10px] text-amber-500">
            {t('catalogs.graph_truncated', 'Showing first 500 — narrow with kind filter')}
          </span>
        )}
        <span className="text-[10px] text-gray-400">
          {displayedGraph.nodes.length} {t('catalogs.graph_nodes', 'nodes')}
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
            {t('catalogs.graph_empty', 'No concepts to visualize.')}
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

      {/* Hidden-kind dimming chips (client-side, doesn't reload) */}
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
