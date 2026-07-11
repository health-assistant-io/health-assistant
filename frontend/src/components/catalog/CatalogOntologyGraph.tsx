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
import { GraphNodeDetail } from './GraphNodeDetail';
import { GraphNodeContextMenu } from './GraphNodeContextMenu';
import { GraphRelationFilter } from './GraphRelationFilter';
import { DynamicIcon } from '../ui/DynamicIcon';
import { getCatalogGraph } from '../../services/catalogService';
import {
  CONCEPT_KIND_LABELS,
  KIND_COLORS,
  CATALOG_TYPE_COLORS,
  CATALOG_TYPE_LABELS,
  CATALOG_TYPE_ICONS,
  type ConceptKind,
} from '../../types/concept';

const ALL_CATALOG_TYPES = Object.keys(CATALOG_TYPE_LABELS);

interface CatalogOntologyGraphProps {
  /** Called when a node is double-clicked (focus). Carries the node's catalog
   *  type so the workspace can navigate with the correct ``?type=`` (the
   *  ontology graph is cross-catalog — a clicked node may belong to a
   *  different type than the one currently browsed). */
  onFocusNode?: (node: { id: string; type?: string | null }) => void;
  /** Bump to force a refetch without remounting. */
  refreshKey?: number;
}

export const CatalogOntologyGraph: React.FC<CatalogOntologyGraphProps> = ({
  onFocusNode,
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
  const [includeIsolated, setIncludeIsolated] = useState(false);

  // Client-side filters
  const [selectedNode, setSelectedNode] = useState<string | undefined>();
  const [depth, setDepth] = useState(0);
  const [hiddenKinds, setHiddenKinds] = useState<string[]>([]);
  const [hiddenRelations, setHiddenRelations] = useState<Set<string>>(new Set());

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
        include_isolated: includeIsolated,
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
            type: n.type,
            icon: n.icon,
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
  }, [activeTypes, activeKinds, includeIsolated]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  // Client-side BFS depth filter from the selected node, then relation filter.
  const displayedGraph = useMemo(() => {
    // First, apply relation-type filter to the raw edges.
    const relEdges = hiddenRelations.size > 0
      ? rawEdges.filter((e) => !hiddenRelations.has(e.relation))
      : rawEdges;

    if (depth === 0 || !selectedNode) {
      return { nodes: rawNodes, edges: relEdges };
    }
    const adj = new Map<string, string[]>();
    for (const e of relEdges) {
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
      edges: relEdges.filter(
        (e) => visited.has(e.source) && visited.has(e.target),
      ),
    };
  }, [rawNodes, rawEdges, depth, selectedNode, hiddenRelations]);

  const toggleRelation = (relation: string) => {
    setHiddenRelations((prev) => {
      const next = new Set(prev);
      if (next.has(relation)) next.delete(relation);
      else next.add(relation);
      return next;
    });
  };

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
      {/* Controls bar — two rows */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 space-y-2">
        {/* Row 1: catalog-type chips + depth + counts */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 mr-1">
            {t('catalogs.graph_types', 'Types')}:
          </span>
          <div className="flex flex-wrap gap-1">
            {ALL_CATALOG_TYPES.map((type) => {
              const active = activeTypes.size === 0 || activeTypes.has(type);
              const typeCount = displayedGraph.nodes.filter(
                (n) => n.type === type,
              ).length;
              return (
                <button
                  key={type}
                  onClick={() => toggleType(type)}
                  title={`${CATALOG_TYPE_LABELS[type]} (${typeCount})`}
                  className={`flex items-center gap-1 px-2 py-0.5 text-[11px] font-bold rounded-full border transition-all ${
                    active
                      ? 'text-white border-transparent'
                      : 'border-gray-200 dark:border-gray-600 text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 opacity-40'
                  }`}
                  style={active ? { backgroundColor: CATALOG_TYPE_COLORS[type] || '#6b7280' } : undefined}
                >
                  <DynamicIcon
                    icon={CATALOG_TYPE_ICONS[type] ?? 'Circle'}
                    className="w-2.5 h-2.5"
                  />
                  {CATALOG_TYPE_LABELS[type]}
                  {typeCount > 0 && (
                    <span className="ml-0.5 px-1 rounded-full text-[9px] bg-black/20">
                      {typeCount}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Show-isolated toggle */}
          <button
            onClick={() => setIncludeIsolated((v) => !v)}
            className={`px-2 py-0.5 text-[11px] font-medium rounded-md border transition-colors ml-1 ${
              includeIsolated
                ? 'bg-indigo-600 text-white border-transparent'
                : 'border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
            title={t('catalogs.graph_isolated_hint', 'Show items with no relations')}
          >
            {t('catalogs.graph_isolated', 'Include isolated')}
          </button>

          {/* Depth chips */}
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
              {t('catalogs.graph_truncated', 'Capped — narrow with filters')}
            </span>
          )}
          <span className="text-[10px] text-gray-400">
            {displayedGraph.nodes.length} {t('catalogs.graph_nodes', 'nodes')} ·{' '}
            {displayedGraph.edges.length} {t('catalogs.graph_edges', 'edges')}
          </span>
        </div>

        {/* Row 2: concept-kind sub-chips (only when concepts are active) */}
        {conceptActive && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
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
          </div>
        )}

        {/* Row 3: relation-type (edge) filter chips.
            Uses rawEdges (NOT displayedGraph.edges) so the chips persist after
            a relation is hidden — the filter must show what *can* be toggled,
            not just what's currently visible. */}
        <GraphRelationFilter
          edges={rawEdges}
          hidden={hiddenRelations}
          onToggle={toggleRelation}
        />
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
              // Single-click is purely local (show detail card). Navigation
              // (URL change + catalog reload) happens on double-click below —
              // doing both on single-click caused a 3s reload delay.
              setSelectedNode(id);
            }}
            onFocusNode={(id) => {
              setSelectedNode(id);
              onFocusNode?.({ id, type: rawNodes.find((n) => n.id === id)?.type });
            }}
            onClearSelection={() => setSelectedNode(undefined)}
            renderNodeDetail={({ node, degree, onClose, onFocus }) => (
              <GraphNodeDetail
                node={node}
                degree={degree}
                onClose={onClose}
                onFocus={onFocus}
              />
            )}
            renderContextMenu={({ x, y, node, onClose, onFocus }) => (
              <GraphNodeContextMenu
                x={x}
                y={y}
                type={node.type ?? ''}
                id={node.id}
                onClose={onClose}
                onFocus={onFocus}
              />
            )}
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
