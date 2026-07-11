import React, { useMemo, useCallback, useEffect, useRef, useState, createContext, useContext } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Panel,
  NodeToolbar,
  Handle,
  Position,
  BaseEdge,
  getBezierPath,
  useStore,
  type Node,
  type Edge,
  type EdgeProps,
  type ReactFlowInstance,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
} from 'reactflow';
import { forceSimulation, forceManyBody, forceLink, forceCollide, forceX, forceY } from 'd3-force';
import { toPng } from 'html-to-image';
import { Download, Search, ChevronDown, ChevronRight } from 'lucide-react';
import { DynamicIcon } from './DynamicIcon';

export interface ConceptGraphNode {
  id: string;
  name: string;
  /** Used for the node color + label badge. String (not ConceptKind) so the
   *  graph can also render non-concept endpoints like ``'anatomy'``. */
  primary_kind?: string | null;
  /** All kind tags — a node stays visible if ANY of its kinds is not hidden. */
  kinds?: string[];
  color?: string | null;
  /** The catalog type of this node (e.g. 'biomarker', 'medication',
   *  'concept'). Required for "Open in catalog" / "Open in domain" actions
   *  and for correct cross-type navigation from the ontology graph. */
  type?: string | null;
  /** Optional lucide icon config (from the registry resolver). */
  icon?: { type: string; value: string } | null;
}

export interface ConceptGraphEdgeData {
  id: string;
  source: string;
  target: string;
  relation: string;
}

/** The identity fields a detail-card renderer needs. Mirrors the relevant
 *  subset of {@link ConceptGraphNode}. */
export interface GraphNodeSummary {
  id: string;
  name: string;
  type?: string | null;
  primary_kind?: string | null;
  color?: string | null;
  icon?: { type: string; value: string } | null;
}

/** Renderer for the floating detail card shown when a node is selected.
 *  Provided by the consumer (e.g. ``<GraphNodeDetail>``) so this generic
 *  renderer stays free of catalog-domain imports. Omit → no card (selection
 *  only highlights the node). */
export type RenderNodeDetail = (args: {
  node: GraphNodeSummary;
  degree: number;
  onClose: () => void;
  onFocus: () => void;
}) => React.ReactNode;

interface ConceptGraphViewProps {
  nodes: ConceptGraphNode[];
  edges: ConceptGraphEdgeData[];
  selectedNodeId?: string;
  hiddenKinds?: string[];
  onSelectNode?: (id: string) => void;
  onFocusNode?: (id: string) => void;
  /** Called when the user clicks empty canvas or the detail card's close
   *  button. The parent should clear its ``selectedNodeId``. */
  onClearSelection?: () => void;
  /** See {@link RenderNodeDetail}. */
  renderNodeDetail?: RenderNodeDetail;
  /** Optional renderer for the right-click context menu. Receives the cursor
   *  position + node identity; the consumer renders a Portaled menu (e.g.
   *  ``<GraphNodeContextMenu>``). Omit → no context menu. */
  renderContextMenu?: (args: {
    x: number;
    y: number;
    node: GraphNodeSummary;
    onClose: () => void;
    onFocus: () => void;
  }) => React.ReactNode;
  className?: string;
}

export const KIND_COLORS: Record<string, string> = {
  specialty: '#3b82f6',
  examination_category: '#f97316',
  event_category: '#ec4899',
  biomarker_class: '#dc2626',
  biomarker_panel: '#f59e0b',
  anatomy_class: '#10b981',
  body_system: '#8b5cf6',
  medication_class: '#06b6d4',
  disease: '#ef4444',
  document_category: '#6b7280',
  procedure: '#14b8a6',
  lifestyle: '#84cc16',
  factor: '#eab308',
  symptom: '#f97316',
  organ: '#a855f7',
  vaccine_class: '#6366f1',
  // Not a ConceptKind — used only for anatomy_structure endpoints surfaced
  // in the graph when the "Show anatomy" toggle is on.
  anatomy: '#10b981',
};

export const RELATION_COLORS: Record<string, string> = {
  EXAMINES: '#3b82f6',
  PERFORMS: '#10b981',
  ORDERS: '#f59e0b',
  MEMBER_OF: '#8b5cf6',
  PART_OF: '#6b7280',
  TREATS: '#ef4444',
  PREVENTS: '#14b8a6',
  INDICATES: '#ec4899',
  MONITORS: '#06b6d4',
  RISK_OF: '#dc2626',
  SCREENS_FOR: '#a855f7',
  CORRELATES_WITH: '#94a3b8',
  CONTRAINDICATES: '#991b1b',
  CAUSED_BY: '#7c2d12',
  LOCATED_IN: '#365314',
  HAS_SPECIALTY: '#3b82f6',
  CLASSIFIED_AS: '#6b7280',
};

// Context bridge so the custom node type (registered via `nodeTypes`, which
// ReactFlow instantiates internally) can reach the parent's selection +
// detail-render callbacks without prop-drilling through NodeProps.
interface GraphNodeCtx {
  renderNodeDetail?: RenderNodeDetail;
  clearSelection: () => void;
  focusNode: (id: string) => void;
}
const GraphNodeContext = createContext<GraphNodeCtx | null>(null);

// Extra fields we attach to each node's `data` for the custom renderer.
interface GraphItemNodeData {
  label?: React.ReactNode;
  name?: string;
  type?: string | null;
  primary_kind?: string | null;
  kinds?: string[];
  color?: string | null;
  icon?: { type: string; value: string } | null;
  degree?: number;
  isSelected?: boolean;
}

/** Custom node: renders the standard label body, plus a floating
 *  `<NodeToolbar>` detail card (when a renderer is provided and the node is
 *  selected). NodeToolbar auto-tracks pan/zoom and avoids clipping.
 *
 *  Includes invisible `<Handle>` components on all 4 sides so ReactFlow can
 *  attach edges — custom node types don't get the default node's built-in
 *  handles, so without these edges won't render. */
const HIDDEN_HANDLE_STYLE = { opacity: 0 };

// ── Floating edges ──────────────────────────────────────────────────────────
// Standard ReactFlow technique for force-directed graphs: a custom edge type
// that computes the optimal connection point on each node's border (where the
// center-to-center line intersects the bounding box), so edges never make
// unnecessary curves — they exit/enter from the side closest to the other
// node. Re-computes on drag so it's always correct.

/** Intersection of the center-to-center line with a node's bounding box. */
function getNodeIntersection(
  node: any,
  otherNode: any,
): { x: number; y: number } {
  const nPos = node.positionAbsolute ?? node.position ?? { x: 0, y: 0 };
  const oPos = otherNode.positionAbsolute ?? otherNode.position ?? { x: 0, y: 0 };
  const nw = (node.width ?? 120) / 2;
  const nh = (node.height ?? 40) / 2;
  const ow = (otherNode.width ?? 120) / 2;
  const oh = (otherNode.height ?? 40) / 2;

  const cx = nPos.x + nw;
  const cy = nPos.y + nh;
  const ox = oPos.x + ow;
  const oy = oPos.y + oh;
  const dx = ox - cx;
  const dy = oy - cy;

  if (dx === 0 && dy === 0) return { x: cx, y: cy };

  const tX = Math.abs(dx) > 0 ? nw / Math.abs(dx) : Infinity;
  const tY = Math.abs(dy) > 0 ? nh / Math.abs(dy) : Infinity;
  const t = Math.min(tX, tY);
  return { x: cx + dx * t, y: cy + dy * t };
}

/** Which side of the node the intersection point is on (for bezier control). */
function getEdgeSide(node: any, point: { x: number; y: number }): Position {
  const nPos = node.positionAbsolute ?? node.position ?? { x: 0, y: 0 };
  const w = node.width ?? 120;
  const h = node.height ?? 40;
  const rx = point.x - nPos.x;
  const ry = point.y - nPos.y;
  if (rx <= 1) return Position.Left;
  if (rx >= w - 1) return Position.Right;
  if (ry <= 1) return Position.Top;
  if (ry >= h - 1) return Position.Bottom;
  return Position.Top;
}

const FloatingEdge: React.FC<EdgeProps> = ({
  id,
  source,
  target,
  markerEnd,
  style,
  label,
  labelStyle,
  labelBgStyle,
  labelBgPadding,
  labelBgBorderRadius,
}) => {
  const sourceNode = useStore((s: any) => s.nodeInternals.get(source));
  const targetNode = useStore((s: any) => s.nodeInternals.get(target));
  if (!sourceNode || !targetNode) return null;

  const sPt = getNodeIntersection(sourceNode, targetNode);
  const tPt = getNodeIntersection(targetNode, sourceNode);
  const sPos = getEdgeSide(sourceNode, sPt);
  const tPos = getEdgeSide(targetNode, tPt);

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX: sPt.x,
    sourceY: sPt.y,
    sourcePosition: sPos,
    targetX: tPt.x,
    targetY: tPt.y,
    targetPosition: tPos,
  });

  return (
    <BaseEdge
      id={id}
      path={edgePath}
      labelX={labelX}
      labelY={labelY}
      label={label}
      labelStyle={labelStyle}
      labelShowBg
      labelBgStyle={labelBgStyle}
      labelBgPadding={labelBgPadding}
      labelBgBorderRadius={labelBgBorderRadius}
      markerEnd={markerEnd}
      style={style}
    />
  );
};

const GraphItemNode: React.FC<{ id: string; data: GraphItemNodeData }> = ({
  id,
  data,
}) => {
  const ctx = useContext(GraphNodeContext);
  const hasDetail = !!ctx?.renderNodeDetail;
  return (
    <>
      {/* Handles exist so ReactFlow considers the node a valid edge endpoint.
          Their position is irrelevant — the floating edge type computes the
          actual connection point on the node's border dynamically. */}
      <Handle type="target" position={Position.Left} isConnectable={false} style={HIDDEN_HANDLE_STYLE} />
      <Handle type="source" position={Position.Right} isConnectable={false} style={HIDDEN_HANDLE_STYLE} />

      {/* Always mounted so ReactFlow can track + position it; visibility
          toggled via ``isVisible`` (conditional mount breaks positioning). */}
      {hasDetail && (
        <NodeToolbar isVisible={data.isSelected} position={Position.Top} offset={8}>
          {/* Stop all interaction events from reaching the graph pane so
              scrolling/dragging inside the popup doesn't pan/zoom the graph. */}
          <div
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            onWheel={(e) => e.stopPropagation()}
          >
          {ctx!.renderNodeDetail!({
            node: {
              id,
              name: data.name ?? '',
              type: data.type,
              primary_kind: data.primary_kind,
              color: data.color,
              icon: data.icon,
            },
            degree: data.degree ?? 0,
            onClose: () => ctx!.clearSelection(),
            onFocus: () => ctx!.focusNode(id),
          })}
          </div>
        </NodeToolbar>
      )}
      {data.label}
    </>
  );
};

const NODE_TYPES = { graphItem: GraphItemNode };
const EDGE_TYPES = { floating: FloatingEdge };

/** Collapsible color legend — maps node-kind and edge-relation colors to
 *  their labels. Renders as a compact expandable card (bottom-left Panel). */
const GraphLegend: React.FC = () => {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 shadow-sm overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium text-slate-500 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-600 w-full"
      >
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        Legend
      </button>
      {open && (
        <div className="flex gap-3 px-2.5 pb-2 pt-1 max-w-[420px]">
          <div className="space-y-0.5">
            <p className="text-[9px] font-bold uppercase text-slate-400 mb-0.5">Nodes</p>
            {Object.entries(KIND_COLORS).slice(0, 8).map(([k, c]) => (
              <div key={k} className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: c }} />
                <span className="text-[10px] text-slate-500 dark:text-slate-400 capitalize">
                  {k.replace(/_/g, ' ')}
                </span>
              </div>
            ))}
          </div>
          <div className="space-y-0.5">
            <p className="text-[9px] font-bold uppercase text-slate-400 mb-0.5">Edges</p>
            {Object.entries(RELATION_COLORS).slice(0, 8).map(([k, c]) => (
              <div key={k} className="flex items-center gap-1.5">
                <span className="w-2.5 h-0.5 rounded-sm" style={{ backgroundColor: c }} />
                <span className="text-[10px] text-slate-500 dark:text-slate-400 capitalize">
                  {k.replace(/_/g, ' ')}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export const ConceptGraphView: React.FC<ConceptGraphViewProps> = ({
  nodes,
  edges,
  selectedNodeId,
  hiddenKinds = [],
  onSelectNode,
  onFocusNode,
  onClearSelection,
  renderNodeDetail,
  renderContextMenu,
  // Default 'h-full' so the graph fills a height-defined parent (callers MUST
  // give the parent a height — React Flow needs explicit dimensions). A caller
  // can pass its own height class (e.g. 'h-[500px]') without conflicting.
  className = 'h-full',
}) => {
  const hiddenSet = useMemo(() => new Set(hiddenKinds), [hiddenKinds]);

  // Search-to-find + hover state.
  const [searchTerm, setSearchTerm] = useState('');
  const lowerSearch = searchTerm.trim().toLowerCase();
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  // ── 1. Compute layout ONLY when raw graph data changes (not on selection) ──
  const { layoutNodes, layoutEdges } = useMemo(() => {
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    const connectedIds = new Set<string>();
    // Degree = number of edges touching a node (either direction).
    const degree = new Map<string, number>();
    for (const e of edges) {
      connectedIds.add(e.source);
      connectedIds.add(e.target);
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }

    const visibleNodes = nodes;

    // d3-force layout
    interface SimNode { id: string; x: number; y: number; vx: number; vy: number }
    interface SimLink { source: string; target: string }

    const simNodes: SimNode[] = visibleNodes.map((n) => ({ id: n.id, x: 0, y: 0, vx: 0, vy: 0 }));
    const simLinks: SimLink[] = edges
      .filter((e) => connectedIds.has(e.source) && connectedIds.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }));

    const simulation = forceSimulation<SimNode>(simNodes)
      .force('charge', forceManyBody().strength(-180))
      .force('link', forceLink<SimNode, SimLink>(simLinks).id((d) => d.id).distance(100).strength(0.6))
      .force('collide', forceCollide(85))
      .force('x', forceX(0).strength(0.04))
      .force('y', forceY(0).strength(0.04))
      .stop();

    for (let i = 0; i < 500; i++) simulation.tick();

    const positions = new Map<string, { x: number; y: number }>();
    for (const n of simNodes) positions.set(n.id, { x: n.x, y: n.y });

    const fnodes: Node[] = nodes
      .filter((n) => positions.has(n.id))
      .map((n) => {
        const pos = positions.get(n.id)!;
        const bg = n.color || KIND_COLORS[n.primary_kind || ''] || '#6b7280';
        const labelKind = n.primary_kind || (n.kinds?.[0] ?? '');
        return {
          id: n.id,
          data: {
            label: (
              <div className="flex items-center gap-1.5 leading-tight max-w-[160px]">
                {n.icon ? (
                  <DynamicIcon
                    icon={{ type: n.icon.type as 'lucide' | 'custom_svg', value: n.icon.value }}
                    className="w-3 h-3 shrink-0 opacity-90"
                  />
                ) : null}
                <div className="flex flex-col items-start min-w-0">
                  <span className="truncate">{n.name}</span>
                  <span className="text-[8px] opacity-60 uppercase tracking-wide">{labelKind.replace(/_/g, ' ')}</span>
                </div>
              </div>
            ),
            name: n.name,
            primary_kind: n.primary_kind,
            kinds: n.kinds,
            type: n.type,
            icon: n.icon,
            color: n.color,
            degree: degree.get(n.id) ?? 0,
          },
          type: 'graphItem',
          position: pos,
          style: {
            color: '#fff',
            borderRadius: '10px',
            background: bg,
            border: '1px solid rgba(255,255,255,0.25)',
            fontSize: '11px',
            fontWeight: 500,
            padding: '5px 10px',
            cursor: 'pointer',
            opacity: 1,
            textAlign: 'left' as const,
            boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
          },
        };
      });

    const fedges: Edge[] = edges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'floating',
        label: e.relation.replace(/_/g, ' '),
        labelStyle: { fontSize: 9, fill: '#6b7280' },
        labelBgStyle: { fill: '#f9fafb' },
        labelBgPadding: [4, 2] as [number, number],
        style: {
          stroke: RELATION_COLORS[e.relation] || '#94a3b8',
          strokeWidth: 1.5,
        },
        animated: e.relation === 'EXAMINES' || e.relation === 'PERFORMS',
      }));

    return { layoutNodes: fnodes, layoutEdges: fedges };
  }, [nodes, edges]);

  // ── 2. ReactFlow internal state — gives it control over drag positions ──
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(layoutNodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(layoutEdges);

  // ── 3. When raw data changes, reset positions from layout ──
  useEffect(() => { setRfNodes(layoutNodes); }, [layoutNodes, setRfNodes]);
  useEffect(() => { setRfEdges(layoutEdges); }, [layoutEdges, setRfEdges]);

  // ── 3b. After layout recomputes (e.g. depth/filter changed), fit the view.
  //    Runs ONLY on layoutNodes change — NOT on selection change, so
  //    deselecting a node doesn't move/resize the viewport. ──
  const rfInstance = useRef<ReactFlowInstance | null>(null);
  useEffect(() => {
    if (!rfInstance.current) return;
    const inst = rfInstance.current;
    const t = setTimeout(() => {
      inst.fitView({ padding: 0.25, duration: 400 });
    }, 50);
    return () => clearTimeout(t);
  }, [layoutNodes]);

  // ── 3c. Center on a node when it's selected (but don't touch the viewport
  //    when deselecting — leave the camera where the user left it). ──
  useEffect(() => {
    if (!rfInstance.current || !selectedNodeId) return;
    const inst = rfInstance.current;
    const node = layoutNodes.find((n) => n.id === selectedNodeId);
    if (!node) return;
    const t = setTimeout(() => {
      inst.setCenter(node.position.x, node.position.y, { zoom: 1, duration: 400 });
    }, 50);
    return () => clearTimeout(t);
  }, [selectedNodeId, layoutNodes]);

  // ── 4. Style updates (selection / hidden / search) — preserves drag ──
  // NOTE: ``hoveredNodeId`` is intentionally NOT in the deps — rebuilding all
  // node objects on every mouse enter/leave kills performance with large
  // graphs. Hover styling is CSS-driven via the <style> tag below.
  useEffect(() => {
    setRfNodes((prev) =>
      prev.map((n) => {
        const data = (n.data as any) ?? {};
        const nodeKinds: string[] = Array.isArray(data.kinds) && data.kinds.length
          ? data.kinds
          : data.primary_kind
            ? [data.primary_kind]
            : [];
        const isSelected = n.id === selectedNodeId;
        // Multi-kind: a node is dimmed only when ALL of its kinds are hidden.
        const dimmed = nodeKinds.length > 0 && nodeKinds.every((k) => hiddenSet.has(k));
        // Search dimming: non-matching nodes fade when a search is active.
        const name = typeof data.name === 'string' ? data.name.toLowerCase() : '';
        const searchMiss = lowerSearch.length > 0 && !name.includes(lowerSearch);
        return {
          ...n,
          data: { ...data, isSelected },
          style: {
            ...n.style,
            opacity: dimmed ? 0.15 : searchMiss ? 0.2 : 1,
            border: isSelected ? '3px solid #1d4ed8' : '1px solid rgba(255,255,255,0.3)',
            fontWeight: isSelected ? 'bold' : 'normal',
            boxShadow: isSelected
              ? '0 0 0 4px rgba(37,99,235,0.25)'
              : '0 1px 3px rgba(0,0,0,0.15)',
          },
        };
      }),
    );
  }, [selectedNodeId, hiddenSet, setRfNodes, lowerSearch]);

  // ── Right-click context menu state (declared early so click/drag handlers
  //    below can reference closeContextMenu). ──
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    id: string;
    data: GraphItemNodeData;
  } | null>(null);
  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  // Distinguish click from drag: ReactFlow fires onNodeClick after a drag if
  // the mouse-up is on the node. We suppress the click if a drag just ended.
  const didDragRef = useRef(false);
  const onNodeDragStart = useCallback(() => {
    didDragRef.current = false;
  }, []);
  const onNodeDrag = useCallback(() => {
    didDragRef.current = true;
  }, []);
  const onNodeDragStop = useCallback(() => {
    // Reset after a tick so the click handler (fires synchronously after
    // drag-stop) can still read the flag.
    setTimeout(() => { didDragRef.current = false; }, 50);
  }, []);

  const onNodeClick = useCallback(
    (_e: React.MouseEvent, node: Node) => {
      if (didDragRef.current) return; // ignore click right after a drag
      closeContextMenu();
      onSelectNode?.(node.id);
    },
    [onSelectNode, closeContextMenu],
  );

  const onNodeDoubleClick = useCallback(
    (_e: React.MouseEvent, node: Node) => onFocusNode?.(node.id),
    [onFocusNode],
  );

  // Search: Enter centers the graph on the first matching node.
  const onSearchSubmit = useCallback(() => {
    if (!lowerSearch || !rfInstance.current) return;
    const match = rfNodes.find((n) => {
      const name = (n.data as any)?.name;
      return typeof name === 'string' && name.toLowerCase().includes(lowerSearch);
    });
    if (match) {
      rfInstance.current.setCenter(match.position.x, match.position.y, {
        zoom: 1,
        duration: 400,
      });
    }
  }, [lowerSearch, rfNodes]);

  // Hover handlers (quick preview without selecting).
  const onNodeMouseEnter = useCallback(
    (_e: React.MouseEvent, node: Node) => setHoveredNodeId(node.id),
    [],
  );
  const onNodeMouseLeave = useCallback(() => setHoveredNodeId(null), []);

  // Center the graph on a node (used by the detail card's Focus button).
  const focusNode = useCallback(
    (id: string) => {
      if (!rfInstance.current) return;
      const node = rfNodes.find((n) => n.id === id);
      if (node) {
        rfInstance.current.setCenter(node.position.x, node.position.y, {
          zoom: 1,
          duration: 400,
        });
      }
    },
    [rfNodes],
  );

  // Bridge the parent's selection-clear into the custom-node context.
  const clearSelection = useCallback(() => onClearSelection?.(), [onClearSelection]);

  const ctxValue = useMemo<GraphNodeCtx>(
    () => ({ renderNodeDetail, clearSelection, focusNode }),
    [renderNodeDetail, clearSelection, focusNode],
  );

  // Clicking empty canvas closes the detail card (deselects) AND the
  // right-click context menu.
  const onPaneClick = useCallback(() => {
    onClearSelection?.();
    closeContextMenu();
  }, [onClearSelection, closeContextMenu]);

  const onNodeContextMenu = useCallback(
    (e: React.MouseEvent, node: Node) => {
      e.preventDefault();
      const data = (node.data as GraphItemNodeData) ?? {};
      setContextMenu({ x: e.clientX, y: e.clientY, id: node.id, data });
    },
    [],
  );

  // ── Download as PNG — captures the FULL graph, tightly cropped ──
  const wrapperRef = useRef<HTMLDivElement>(null);
  const handleDownload = useCallback(async () => {
    const el = wrapperRef.current?.querySelector('.react-flow__viewport') as HTMLElement | null;
    if (!el || rfNodes.length === 0) return;

    // Compute bounding box from current node positions
    const NODE_W = 170;
    const NODE_H = 56;
    const PAD = 60;
    const xs = rfNodes.map((n) => n.position.x);
    const ys = rfNodes.map((n) => n.position.y);
    const minX = Math.min(...xs) - PAD;
    const minY = Math.min(...ys) - PAD;
    const maxX = Math.max(...xs) + NODE_W + PAD;
    const maxY = Math.max(...ys) + NODE_H + PAD;
    const width = maxX - minX;
    const height = maxY - minY;

    try {
      const dataUrl = await toPng(el, {
        backgroundColor: '#f8fafc',
        pixelRatio: 2,
        width,
        height,
        style: {
          transform: `translate(${-minX}px, ${-minY}px)`,
          width: `${width}px`,
          height: `${height}px`,
        },
        filter: (node) => {
          if (node instanceof HTMLElement) {
            return !node.classList.contains('react-flow__minimap')
                && !node.classList.contains('react-flow__controls')
                && !node.classList.contains('react-flow__panel');
          }
          return true;
        },
      });
      const link = document.createElement('a');
      link.download = 'concept-graph.png';
      link.href = dataUrl;
      link.click();
    } catch {
      /* best-effort */
    }
  }, [rfNodes]);

  if (nodes.length === 0) {
    return (
      <div className={`w-full flex items-center justify-center ${className}`}>
        <p className="text-sm text-gray-400">No concepts to visualize.</p>
      </div>
    );
  }

  return (
    <div className={`w-full ${className}`} ref={wrapperRef}>
      {/* CSS-driven hover effect — avoids rebuilding all node objects on
          every mouse enter/leave (which killed performance on large graphs). */}
      <style>{`
        .react-flow__node-graphItem:not(.selected):hover {
          filter: brightness(1.12);
        }
      `}</style>
      <GraphNodeContext.Provider value={ctxValue}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onInit={(inst) => { rfInstance.current = inst; }}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onNodeContextMenu={onNodeContextMenu}
        onNodeDragStart={onNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1.4 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.15}
        maxZoom={2.5}
      >
        <Background variant={BackgroundVariant.Dots} gap={12} size={1} color="#cbd5e1" />
        <Controls
          showInteractive={false}
          className="!bg-white dark:!bg-dark-surface !border-gray-200 dark:!border-dark-border"
        />
        <MiniMap
          nodeColor={(node) => {
            const bg = node.style?.background as string;
            return typeof bg === 'string' ? bg : '#94a3b8';
          }}
          className="!bg-gray-50 dark:!bg-dark-bg !border-gray-200 dark:!border-dark-border"
          pannable
          zoomable
        />
        <Panel position="top-right">
          <button
            onClick={handleDownload}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-xs font-medium text-slate-600 dark:text-slate-200 shadow-sm hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors"
          >
            <Download className="w-3.5 h-3.5" />
            PNG
          </button>
        </Panel>
        {/* Search-to-find (top-left) */}
        <Panel position="top-left">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 shadow-sm">
              <Search className="w-3.5 h-3.5 text-slate-400" />
              <input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onSearchSubmit();
                  if (e.key === 'Escape') setSearchTerm('');
                }}
                placeholder="Find…"
                className="w-24 bg-transparent text-xs text-slate-600 dark:text-slate-200 placeholder-slate-400 outline-none"
              />
              {lowerSearch && (
                <span className="text-[10px] text-slate-400">
                  {rfNodes.filter((n) => {
                    const name = (n.data as any)?.name;
                    return typeof name === 'string' && name.toLowerCase().includes(lowerSearch);
                  }).length}
                </span>
              )}
            </div>
            {/* Hover quick-preview */}
            {hoveredNodeId && (() => {
              const hn = rfNodes.find((n) => n.id === hoveredNodeId);
              const hd = (hn?.data as any) ?? {};
              return (
                <div className="px-2 py-1 rounded-md bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 shadow-sm text-[10px] text-slate-500 dark:text-slate-300 max-w-[200px] truncate">
                  <span className="font-medium">{hd.name}</span>
                  {hd.type && <span className="opacity-60"> · {hd.type}</span>}
                  <span className="opacity-60"> · {hd.degree ?? 0} rel.</span>
                </div>
              );
            })()}
          </div>
        </Panel>
        {/* Collapsible legend (bottom-left) */}
        <Panel position="bottom-left">
          <GraphLegend />
        </Panel>
      </ReactFlow>
      </GraphNodeContext.Provider>
      {contextMenu && renderContextMenu && (
        renderContextMenu({
          x: contextMenu.x,
          y: contextMenu.y,
          node: {
            id: contextMenu.id,
            name: contextMenu.data.name ?? '',
            type: contextMenu.data.type,
            primary_kind: contextMenu.data.primary_kind,
            color: contextMenu.data.color,
            icon: contextMenu.data.icon,
          },
          onClose: closeContextMenu,
          onFocus: () => focusNode(contextMenu.id),
        })
      )}
    </div>
  );
};
