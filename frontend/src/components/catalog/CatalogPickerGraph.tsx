/**
 * CatalogPickerGraph — the graph-view selection surface for the catalog
 * picker browse modal.
 *
 * Self-contained sibling of {@link CatalogOntologyGraph}, purpose-built for
 * *selecting* items: it owns the same filter strip as the workspace graph
 * (catalog-type chips, concept-kind chips, include-isolated, depth, relation
 * type) so the picker graph and the explorer graph feel identical, but adds
 * two selection-mode affordances:
 *
 * - Nodes whose catalog ``type`` is outside ``allowedTypes`` render **dimmed
 *   and non-clickable** (visible for relational context, never selectable).
 * - Already-picked nodes get a green accent.
 *
 * The type chips default to the field's ``allowedTypes`` (focused view); the
 * user can toggle additional types on to see cross-catalog context. When the
 * field declares a ``conceptKind`` (e.g. ``examination_category``), concepts
 * are narrowed server-side and the kind-chip row is hidden (the field already
 * locked the kind).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Eye, EyeOff } from 'lucide-react';
import {
  ConceptGraphView,
  KIND_COLORS,
} from '../ui/ConceptGraphView';
import type {
  ConceptGraphNode,
  ConceptGraphEdgeData,
} from '../ui/ConceptGraphView';
import { DynamicIcon } from '../ui/DynamicIcon';
import { GraphRelationFilter } from './GraphRelationFilter';
import {
  CATALOG_TYPE_COLORS,
  CATALOG_TYPE_ICONS,
  CATALOG_TYPE_LABELS,
  CONCEPT_KIND_LABELS,
  KIND_COLORS as CONCEPT_KIND_COLORS,
} from '../../types/concept';
import type { ConceptKind } from '../../types/concept';
import { getCatalogGraph } from '../../services/catalogService';
import { LoadingState } from '../ui/LoadingState';
import type { CatalogItem } from '../../types/catalog';

/** Edge cap for the modal render — keeps React Flow responsive. */
const PICKER_GRAPH_EDGE_LIMIT = 2500;
/** The fixed catalog-type registry (mirrors CatalogOntologyGraph). */
const ALL_CATALOG_TYPES = Object.keys(CATALOG_TYPE_LABELS);
const ALL_KINDS = Object.keys(CONCEPT_KIND_LABELS) as ConceptKind[];
const DEPTH_OPTIONS = [0, 1, 2, 3, 4];

export interface CatalogPickerGraphProps {
  /** The catalog types the field accepts. Nodes of other types render dimmed
   *  + non-clickable. When omitted, every node is selectable. Also seeds the
   *  initial type-chip selection (focused view). */
  allowedTypes?: string[];
  /** ConceptKind lock from the field declaration. When set, concepts are
   *  narrowed server-side and the kind-chip row is hidden. */
  conceptKind?: string;
  /** Ids already in the picker's selection — rendered with a green accent. */
  pickedIds: string[];
  /** Toggle a visible, selectable node into/out of the selection. */
  onTogglePick: (item: CatalogItem, catalogType: string) => void;
  mode?: 'single' | 'multi';
}

export const CatalogPickerGraph: React.FC<CatalogPickerGraphProps> = ({
  allowedTypes,
  conceptKind,
  pickedIds,
  onTogglePick,
}) => {
  const { t } = useTranslation();
  const [rawNodes, setRawNodes] = useState<ConceptGraphNode[]>([]);
  const [rawEdges, setRawEdges] = useState<ConceptGraphEdgeData[]>([]);
  const [loading, setLoading] = useState(true);
  const [truncated, setTruncated] = useState(false);

  // Server-side filters.
  const [activeTypes, setActiveTypes] = useState<Set<string>>(
    () => new Set(allowedTypes ?? []),
  );
  const [activeKinds, setActiveKinds] = useState<Set<ConceptKind>>(new Set());
  const [includeIsolated, setIncludeIsolated] = useState(false);

  // Client-side filters.
  const [selectedNode, setSelectedNode] = useState<string | undefined>();
  const [depth, setDepth] = useState(0);
  const [hiddenRelations, setHiddenRelations] = useState<Set<string>>(
    new Set(),
  );

  const allowedSet = useMemo(
    () => (allowedTypes ? new Set(allowedTypes) : null),
    [allowedTypes],
  );

  const conceptActive =
    activeTypes.size === 0 || activeTypes.has('concept');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // When the field locks a conceptKind, it overrides the kind chips (the
      // kind-chip row is hidden in that case).
      const kindParam = conceptKind
        ? conceptKind
        : activeKinds.size > 0
          ? [...activeKinds].join(',')
          : undefined;
      const resp = await getCatalogGraph({
        types: activeTypes.size > 0 ? [...activeTypes].join(',') : undefined,
        kind: kindParam,
        include_isolated: includeIsolated,
        limit: PICKER_GRAPH_EDGE_LIMIT,
      });
      setRawNodes(
        resp.nodes.map((n) => {
          const kindOrType = n.kind || n.type;
          return {
            id: n.id,
            name: n.label || `${n.type}:${n.id.slice(0, 8)}`,
            primary_kind: kindOrType,
            kinds: [kindOrType],
            color:
              n.color ||
              KIND_COLORS[kindOrType as ConceptKind] ||
              CATALOG_TYPE_COLORS[kindOrType] ||
              '#6b7280',
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
      setTruncated(false);
    } finally {
      setLoading(false);
    }
  }, [activeTypes, activeKinds, includeIsolated, conceptKind]);

  useEffect(() => {
    load();
  }, [load]);

  // Client-side depth BFS + relation filter (mirrors CatalogOntologyGraph).
  const displayed = useMemo(() => {
    const relEdges =
      hiddenRelations.size > 0
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

  const nodeIndex = useMemo(() => {
    const m = new Map<string, ConceptGraphNode>();
    for (const n of displayed.nodes) m.set(n.id, n);
    return m;
  }, [displayed.nodes]);

  // Disabled = visible but non-selectable (catalog type outside allowedTypes).
  const disabledNodeIds = useMemo(() => {
    if (!allowedSet) return undefined;
    const s = new Set<string>();
    for (const n of displayed.nodes) {
      if (n.type && !allowedSet.has(n.type)) s.add(n.id);
    }
    return s.size > 0 ? s : undefined;
  }, [displayed.nodes, allowedSet]);

  const pickedNodeIds = useMemo(
    () => (pickedIds.length ? new Set(pickedIds) : undefined),
    [pickedIds],
  );

  const handleSelectNode = useCallback(
    (id: string) => {
      setSelectedNode(id);
      const node = nodeIndex.get(id);
      if (!node) return;
      if (allowedSet && node.type && !allowedSet.has(node.type)) return;
      onTogglePick(
        { id: node.id, name: node.name } as CatalogItem,
        node.type ?? '',
      );
    },
    [nodeIndex, allowedSet, onTogglePick],
  );

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

  return (
    <div className="flex flex-col h-full gap-2">
      {/* Filter strip — mirrors the workspace graph explorer. */}
      <div className="flex flex-col gap-1.5 shrink-0">
        {/* Row 1: type chips + isolated toggle */}
        <div className="flex flex-wrap items-center gap-1.5">
          {ALL_CATALOG_TYPES.map((type) => {
            const active = activeTypes.size === 0 || activeTypes.has(type);
            const count = displayed.nodes.filter((n) => n.type === type).length;
            return (
              <button
                key={type}
                onClick={() => toggleType(type)}
                className={`flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium border transition-all ${
                  active
                    ? 'text-white border-transparent'
                    : 'text-gray-500 dark:text-gray-400 border-gray-300 dark:border-gray-600 opacity-50 hover:opacity-80'
                }`}
                style={
                  active
                    ? { backgroundColor: CATALOG_TYPE_COLORS[type] || '#6b7280' }
                    : undefined
                }
              >
                <DynamicIcon
                  icon={{ type: 'lucide', value: CATALOG_TYPE_ICONS[type] }}
                  className="w-3 h-3"
                />
                <span>{CATALOG_TYPE_LABELS[type]}</span>
                {count > 0 && (
                  <span
                    className={`ml-0.5 px-1 rounded-full text-[9px] ${
                      active ? 'bg-black/20' : 'bg-gray-200 dark:bg-gray-700'
                    }`}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
          <button
            onClick={() => setIncludeIsolated((v) => !v)}
            title={t('catalogs.graph_isolated', 'Include isolated items')}
            className={`flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium border transition-all ${
              includeIsolated
                ? 'bg-indigo-600 text-white border-transparent'
                : 'text-gray-500 dark:text-gray-400 border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
          >
            {includeIsolated ? (
              <Eye className="w-3 h-3" />
            ) : (
              <EyeOff className="w-3 h-3" />
            )}
            <span>{t('catalogs.graph_isolated_short', 'Isolated')}</span>
          </button>
        </div>

        {/* Row 2: kind chips (only when concept visible + no field-level kind lock). */}
        {conceptActive && !conceptKind && (
          <div className="flex flex-wrap items-center gap-1.5">
            {ALL_KINDS.map((kind) => {
              const active =
                activeKinds.size === 0 || activeKinds.has(kind);
              return (
                <button
                  key={kind}
                  onClick={() => toggleKind(kind)}
                  className={`px-2 py-0.5 rounded-full text-[10px] font-medium border transition-all ${
                    active
                      ? 'text-white border-transparent'
                      : 'text-gray-400 border-gray-200 dark:border-gray-700 opacity-60 hover:opacity-100'
                  }`}
                  style={
                    active
                      ? { backgroundColor: CONCEPT_KIND_COLORS[kind] }
                      : undefined
                  }
                >
                  {CONCEPT_KIND_LABELS[kind]}
                </button>
              );
            })}
          </div>
        )}

        {/* Row 3: depth + relation filter */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mr-0.5">
              {t('catalogs.graph_depth', 'Depth')}
            </span>
            {DEPTH_OPTIONS.map((d) => (
              <button
                key={d}
                onClick={() => setDepth(d)}
                disabled={d !== 0 && !selectedNode}
                className={`w-6 h-6 rounded text-[11px] font-bold transition-all disabled:opacity-30 disabled:cursor-not-allowed ${
                  depth === d
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
                }`}
              >
                {d === 0 ? '∞' : d}
              </button>
            ))}
          </div>
          <GraphRelationFilter
            edges={displayed.edges}
            hidden={hiddenRelations}
            onToggle={(rel) =>
              setHiddenRelations((prev) => {
                const next = new Set(prev);
                if (next.has(rel)) next.delete(rel);
                else next.add(rel);
                return next;
              })
            }
          />
        </div>
      </div>

      {/* Canvas */}
      <div className="flex-1 min-h-0 relative">
        {loading ? (
          <LoadingState
            variant="section"
            message={t('catalogs.loading_graph', 'Loading graph…')}
          />
        ) : displayed.nodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-4 py-10">
            <AlertTriangle className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
            <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
              {t('catalogs.picker_graph_empty_title', 'No connected items')}
            </p>
            <p className="text-xs text-gray-400 mt-1">
              {t(
                'catalogs.picker_graph_empty_hint',
                'This catalog has no relationships to show in graph view. Use the list view to browse all items.',
              )}
            </p>
          </div>
        ) : (
          <>
            {truncated && (
              <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                <AlertTriangle className="w-3 h-3" />
                {t(
                  'catalogs.picker_graph_truncated',
                  'Graph capped at {{n}} edges — narrow the type filter or use list view for everything.',
                  { n: PICKER_GRAPH_EDGE_LIMIT },
                )}
              </div>
            )}
            <ConceptGraphView
              nodes={displayed.nodes}
              edges={displayed.edges}
              disabledNodeIds={disabledNodeIds}
              pickedNodeIds={pickedNodeIds}
              selectedNodeId={selectedNode}
              onSelectNode={handleSelectNode}
              onClearSelection={() => setSelectedNode(undefined)}
              showMiniMap={false}
            />
          </>
        )}
      </div>
    </div>
  );
};
