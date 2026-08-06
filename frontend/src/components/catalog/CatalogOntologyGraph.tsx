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
import { X } from 'lucide-react';
import { useIsMobile } from '../../hooks/useMediaQuery';
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

  /** Graph-filter state — owned by the workspace so the toolbar's Filters
   *  button + badge can drive the same state the canvas reads. */
  activeTypes: Set<string>;
  activeKinds: Set<ConceptKind>;
  hiddenRelations: Set<string>;
  hiddenKinds: string[];
  includeIsolated: boolean;
  depth: number;
  selectedNode?: string;

  /** Setters for the controlled filter state above. */
  setActiveTypes: React.Dispatch<React.SetStateAction<Set<string>>>;
  setActiveKinds: React.Dispatch<React.SetStateAction<Set<ConceptKind>>>;
  setHiddenRelations: React.Dispatch<React.SetStateAction<Set<string>>>;
  setHiddenKinds: React.Dispatch<React.SetStateAction<string[]>>;
  setIncludeIsolated: React.Dispatch<React.SetStateAction<boolean>>;
  setDepth: React.Dispatch<React.SetStateAction<number>>;
  setSelectedNode: React.Dispatch<React.SetStateAction<string | undefined>>;

  /** Mobile sheet visibility — workspace-owned so the toolbar Filters button
   *  can open it (the trigger lives in the toolbar, not here). */
  filtersOpen: boolean;
  onFiltersOpenChange: (open: boolean) => void;

  /** Active filter-dimension count, for the sheet header badge. */
  activeFilterCount: number;
}

export const CatalogOntologyGraph: React.FC<CatalogOntologyGraphProps> = ({
  onFocusNode,
  refreshKey = 0,
  activeTypes,
  activeKinds,
  hiddenRelations,
  hiddenKinds,
  includeIsolated,
  depth,
  selectedNode,
  setActiveTypes,
  setActiveKinds,
  setHiddenRelations,
  setHiddenKinds,
  setIncludeIsolated,
  setDepth,
  setSelectedNode,
  filtersOpen,
  onFiltersOpenChange,
  activeFilterCount,
}) => {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [rawNodes, setRawNodes] = useState<ConceptGraphNode[]>([]);
  const [rawEdges, setRawEdges] = useState<ConceptGraphEdgeData[]>([]);
  const [loading, setLoading] = useState(true);
  const [truncated, setTruncated] = useState(false);

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

  const resetFilters = () => {
    setActiveTypes(new Set());
    setActiveKinds(new Set());
    setHiddenRelations(new Set());
    setHiddenKinds([]);
    setIncludeIsolated(false);
    setDepth(0);
  };

  // Close the filter panel/sheet on Escape.
  useEffect(() => {
    if (!filtersOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onFiltersOpenChange(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [filtersOpen, onFiltersOpenChange]);

  // Unified filter sections — one source of truth rendered inline on desktop
  // and inside the mobile bottom sheet. Includes the Dim row (formerly below
  // the canvas) so every filter lives in one place.
  const renderFilterSections = () => (
    <>
      {/* Types + include-isolated + depth */}
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
      </div>

      {/* Kinds (concepts only) */}
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

      {/* Relations (edge types) — uses rawEdges so chips persist after hide */}
      <GraphRelationFilter
        edges={rawEdges}
        hidden={hiddenRelations}
        onToggle={toggleRelation}
      />

      {/* Dim (client-side hidden-kind dimming) */}
      <div className="flex flex-wrap items-center gap-1">
        <span className="text-[10px] text-gray-400 mr-1">
          {t('catalogs.graph_dim', 'Dim')}:
        </span>
        {allKinds.map((kind) => (
          <button
            key={kind}
            onClick={() => toggleHiddenKind(kind)}
            className={`px-1.5 py-0.5 text-[10px] font-bold rounded-full border transition-all ${
              hiddenKinds.includes(kind)
                ? 'bg-gray-500 text-white border-transparent'
                : 'border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
          >
            {CONCEPT_KIND_LABELS[kind]}
          </button>
        ))}
      </div>

      {/* Status: truncation warning + node/edge counts */}
      <div className="flex flex-wrap items-center gap-2 pt-2 mt-1 border-t border-gray-100 dark:border-gray-700">
        {truncated && (
          <span className="text-[10px] text-amber-500">
            {t('catalogs.graph_truncated', 'Capped — narrow with filters')}
          </span>
        )}
        <span className="text-[10px] text-gray-400 ml-auto">
          {displayedGraph.nodes.length} {t('catalogs.graph_nodes', 'nodes')} ·{' '}
          {displayedGraph.edges.length} {t('catalogs.graph_edges', 'edges')}
        </span>
      </div>
    </>
  );

  return (
    <div className="relative flex flex-col h-full min-h-[500px] gap-2">
      {/* === Desktop (md+): slide-in side panel — opened from the toolbar
          === Filters button. Overlays the canvas from the right so the graph
          === keeps full width; close via X or Escape. === */}
      {filtersOpen && (
        <div
          role="dialog"
          aria-modal="false"
          aria-label={t('catalogs.graph_filters', { defaultValue: 'Graph filters' })}
          className="hidden md:flex absolute top-0 right-0 bottom-0 z-[200] w-80 flex-col bg-white dark:bg-dark-surface border-l border-gray-200 dark:border-dark-border shadow-2xl animate-in slide-in-from-right duration-300"
        >
          {/* Header */}
          <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-gray-100 dark:border-dark-border">
            <div className="flex items-center gap-2 min-w-0">
              <h3 className="text-sm font-bold text-gray-700 dark:text-dark-text truncate">
                {t('catalogs.graph_filters', { defaultValue: 'Graph Filters' })}
              </h3>
              {activeFilterCount > 0 && (
                <span className="min-w-[18px] h-[18px] px-1 inline-flex items-center justify-center text-[10px] font-bold text-white bg-blue-500 rounded-full shrink-0">
                  {activeFilterCount}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => onFiltersOpenChange(false)}
              className="p-1.5 -mr-1 text-gray-400 hover:text-gray-600 dark:hover:text-dark-text shrink-0"
              aria-label={t('common.close', { defaultValue: 'Close' })}
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Scrollable filter sections */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 custom-scrollbar">
            {renderFilterSections()}
          </div>

          {/* Footer — reset */}
          <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-100 dark:border-dark-border">
            <button
              type="button"
              onClick={resetFilters}
              disabled={activeFilterCount === 0}
              className="text-xs font-bold text-gray-500 dark:text-dark-muted hover:text-red-500 disabled:opacity-40 disabled:hover:text-gray-500 transition-colors"
            >
              {t('filters.clear_all', { defaultValue: 'Clear all' })}
            </button>
          </div>
        </div>
      )}

      {/* === Mobile (< md): bottom sheet — opened from the toolbar Filters
           === button (workspace-owned `filtersOpen`). === */}
      {filtersOpen && (
        <>
          <div
            className="md:hidden fixed inset-0 bg-black/40 z-[200]"
            onClick={() => onFiltersOpenChange(false)}
            aria-hidden
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t('catalogs.graph_filters', { defaultValue: 'Graph filters' })}
            className="md:hidden fixed inset-x-0 bottom-0 z-[201] max-h-[78vh] flex flex-col bg-white dark:bg-dark-surface rounded-t-2xl shadow-2xl animate-in slide-in-from-bottom duration-300"
          >
            {/* Header — drag handle + title + active badge + close */}
            <div className="flex items-center justify-between gap-2 px-4 pt-2 pb-3 border-b border-gray-100 dark:border-dark-border">
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <div className="w-10 h-1 bg-gray-300 dark:bg-gray-600 rounded-full shrink-0" aria-hidden />
                <h3 className="text-sm font-bold text-gray-700 dark:text-dark-text truncate">
                  {t('catalogs.graph_filters', { defaultValue: 'Graph Filters' })}
                </h3>
                {activeFilterCount > 0 && (
                  <span className="min-w-[18px] h-[18px] px-1 inline-flex items-center justify-center text-[10px] font-bold text-white bg-blue-500 rounded-full shrink-0">
                    {activeFilterCount}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => onFiltersOpenChange(false)}
                className="p-1.5 -mr-1 text-gray-400 hover:text-gray-600 dark:hover:text-dark-text shrink-0"
                aria-label={t('common.close', { defaultValue: 'Close' })}
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Scrollable filter sections */}
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 custom-scrollbar">
              {renderFilterSections()}
            </div>

            {/* Footer — reset + done */}
            <div className="flex items-center justify-between gap-2 px-4 py-3 border-t border-gray-100 dark:border-dark-border">
              <button
                type="button"
                onClick={resetFilters}
                disabled={activeFilterCount === 0}
                className="text-xs font-bold text-gray-500 dark:text-dark-muted hover:text-red-500 disabled:opacity-40 disabled:hover:text-gray-500 transition-colors"
              >
                {t('filters.clear_all', { defaultValue: 'Clear all' })}
              </button>
              <button
                type="button"
                onClick={() => onFiltersOpenChange(false)}
                className="px-4 py-2 text-xs font-bold rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
              >
                {t('common.done', { defaultValue: 'Done' })}
              </button>
            </div>
          </div>
        </>
      )}

      {/* Graph canvas (shared) */}
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
            showMiniMap={!isMobile}
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
    </div>
  );
};
