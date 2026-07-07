import React, { useMemo, useCallback, useEffect, useRef } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Panel,
  type Node,
  type Edge,
  type ReactFlowInstance,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
} from 'reactflow';
import { forceSimulation, forceManyBody, forceLink, forceCollide, forceX, forceY } from 'd3-force';
import { toPng } from 'html-to-image';
import { Download } from 'lucide-react';

export interface ConceptGraphNode {
  id: string;
  name: string;
  /** Used for the node color + label badge. String (not ConceptKind) so the
   *  graph can also render non-concept endpoints like ``'anatomy'``. */
  primary_kind?: string | null;
  /** All kind tags — a node stays visible if ANY of its kinds is not hidden. */
  kinds?: string[];
  color?: string | null;
}

export interface ConceptGraphEdgeData {
  id: string;
  source: string;
  target: string;
  relation: string;
}

interface ConceptGraphViewProps {
  nodes: ConceptGraphNode[];
  edges: ConceptGraphEdgeData[];
  selectedNodeId?: string;
  hiddenKinds?: string[];
  onSelectNode?: (id: string) => void;
  onFocusNode?: (id: string) => void;
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

export const ConceptGraphView: React.FC<ConceptGraphViewProps> = ({
  nodes,
  edges,
  selectedNodeId,
  hiddenKinds = [],
  onSelectNode,
  onFocusNode,
  className = '',
}) => {
  const hiddenSet = useMemo(() => new Set(hiddenKinds), [hiddenKinds]);

  // ── 1. Compute layout ONLY when raw graph data changes (not on selection) ──
  const { layoutNodes, layoutEdges } = useMemo(() => {
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    const connectedIds = new Set<string>();
    for (const e of edges) {
      connectedIds.add(e.source);
      connectedIds.add(e.target);
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
        const bg = KIND_COLORS[n.primary_kind || ''] || '#6b7280';
        const labelKind = n.primary_kind || (n.kinds?.[0] ?? '');
        return {
          id: n.id,
          data: {
            label: (
              <div className="flex flex-col items-center leading-tight">
                <span>{n.name}</span>
                <span className="text-[8px] opacity-60 uppercase tracking-wide">{labelKind.replace(/_/g, ' ')}</span>
              </div>
            ),
            primary_kind: n.primary_kind,
            kinds: n.kinds,
          },
          position: pos,
          style: {
            color: '#fff',
            borderRadius: '8px',
            background: bg,
            border: '1px solid rgba(255,255,255,0.3)',
            fontSize: '11px',
            fontWeight: 'normal',
            padding: '6px 10px',
            cursor: 'pointer',
            opacity: 1,
            textAlign: 'center' as const,
          },
        };
      });

    const fedges: Edge[] = edges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
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

  // ── 3b. After layout recomputes (e.g. depth filter changed), recenter ──
  const rfInstance = useRef<ReactFlowInstance | null>(null);
  useEffect(() => {
    if (!rfInstance.current) return;
    const inst = rfInstance.current;
    const t = setTimeout(() => {
      if (selectedNodeId) {
        const node = layoutNodes.find((n) => n.id === selectedNodeId);
        if (node) {
          inst.setCenter(node.position.x, node.position.y, { zoom: 1, duration: 400 });
          return;
        }
      }
      inst.fitView({ padding: 0.25, duration: 400 });
    }, 50);
    return () => clearTimeout(t);
  }, [layoutNodes, selectedNodeId]);

  // ── 4. Style updates (selection / hidden) — preserves drag positions ──
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
        return {
          ...n,
          style: {
            ...n.style,
            opacity: dimmed ? 0.15 : 1,
            border: isSelected ? '3px solid #1d4ed8' : '1px solid rgba(255,255,255,0.3)',
            fontWeight: isSelected ? 'bold' : 'normal',
            boxShadow: isSelected ? '0 0 0 4px rgba(37,99,235,0.25)' : undefined,
          },
        };
      }),
    );
  }, [selectedNodeId, hiddenSet, setRfNodes]);

  const onNodeClick = useCallback(
    (_e: React.MouseEvent, node: Node) => onSelectNode?.(node.id),
    [onSelectNode],
  );

  const onNodeDoubleClick = useCallback(
    (_e: React.MouseEvent, node: Node) => onFocusNode?.(node.id),
    [onFocusNode],
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
      <div className={`w-full h-full flex items-center justify-center ${className}`}>
        <p className="text-sm text-gray-400">No concepts to visualize.</p>
      </div>
    );
  }

  return (
    <div className={`w-full h-full ${className}`} ref={wrapperRef}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onInit={(inst) => { rfInstance.current = inst; }}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
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
      </ReactFlow>
    </div>
  );
};
