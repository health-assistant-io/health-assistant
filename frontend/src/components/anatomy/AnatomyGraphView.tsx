import React, { useMemo, useCallback } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  BackgroundVariant,
} from 'reactflow';
import 'reactflow/dist/style.css';
import type {
  AnatomyGraphEdge,
  AnatomyGraphNodeItem,
  AnatomyCategory,
} from '../../types/anatomy';
import { CATEGORY_COLORS } from '../../types/anatomy';
import { useTranslation } from 'react-i18next';

interface Props {
  rootId: string;
  nodes: AnatomyGraphNodeItem[];
  edges: AnatomyGraphEdge[];
  /** Node currently highlighted by the inspector (click). */
  selectedNodeId?: string;
  /** Categories to dim (legend toggle). */
  hiddenCategories?: AnatomyCategory[];
  /** Click selects a node (never navigates). */
  onSelectNode: (id: string) => void;
  /** Double-click re-centres the graph on a node (in-modal navigation). */
  onFocusNode?: (id: string) => void;
  className?: string;
}

const Y_SPACING = 150;
const X_SPACING = 180;

export const AnatomyGraphView: React.FC<Props> = ({
  rootId,
  nodes,
  edges,
  selectedNodeId,
  hiddenCategories,
  onSelectNode,
  onFocusNode,
  className = '',
}) => {
  const { t } = useTranslation();

  const hiddenSet = useMemo(
    () => new Set<AnatomyCategory>(hiddenCategories ?? []),
    [hiddenCategories]
  );

  const { flowNodes, flowEdges } = useMemo(() => {
    const nodeById = new Map<string, AnatomyGraphNodeItem>(nodes.map((n) => [n.id, n]));
    const depthOf = (id: string) => nodeById.get(id)?.depth ?? 0;

    // Direction sign: -1 = incoming side (above), +1 = outgoing side (below).
    const signFor = (id: string): number => {
      for (const e of edges) {
        if (e.source_id === id && depthOf(e.target_id) === depthOf(id) - 1) return -1;
        if (e.target_id === id && depthOf(e.source_id) === depthOf(id) - 1) return 1;
      }
      return 1;
    };

    // Bucket non-root nodes by (depth, sign) so each band lays out horizontally.
    const buckets = new Map<string, AnatomyGraphNodeItem[]>();
    for (const n of nodes) {
      if (n.id === rootId) continue;
      const key = `${n.depth}:${signFor(n.id)}`;
      const arr = buckets.get(key);
      if (arr) arr.push(n);
      else buckets.set(key, [n]);
    }
    const positions = new Map<string, { x: number; y: number }>();
    positions.set(rootId, { x: 0, y: 0 });
    for (const arr of buckets.values()) {
      const count = arr.length;
      arr.forEach((n, idx) => {
        const sign = signFor(n.id);
        const x = (idx - (count - 1) / 2) * X_SPACING;
        const y = sign * n.depth * Y_SPACING;
        positions.set(n.id, { x, y });
      });
    }

    const isDimmed = (cat: AnatomyCategory) => hiddenSet.has(cat);

    const flowNodes: Node[] = nodes.map((n) => {
      const pos = positions.get(n.id) ?? { x: 0, y: 0 };
      const isRoot = n.id === rootId;
      const isSelected = n.id === selectedNodeId;
      const dimmed = isDimmed(n.category);
      return {
        id: n.id,
        data: { label: n.name },
        position: pos,
        style: {
          color: '#fff',
          borderRadius: '8px',
          background: CATEGORY_COLORS[n.category],
          border: isSelected
            ? `3px solid #1d4ed8`
            : isRoot
              ? `3px solid #1e40af`
              : `1px solid #cbd5e1`,
          fontSize: isRoot ? '13px' : '11px',
          fontWeight: isRoot || isSelected ? 'bold' : 'normal',
          padding: isRoot ? '8px 14px' : '6px 10px',
          cursor: 'pointer',
          opacity: dimmed ? 0.12 : 1,
          boxShadow: isSelected ? '0 0 0 4px rgba(37,99,235,0.25)' : undefined,
        },
      };
    });

    const flowEdges: Edge[] = edges.map((e) => {
      const srcNode = nodeById.get(e.source_id);
      const tgtNode = nodeById.get(e.target_id);
      const dimmed =
        !!srcNode && isDimmed(srcNode.category) && !!tgtNode && isDimmed(tgtNode.category);
      return {
        id: `e-${e.source_id}-${e.target_id}-${e.relation_type}`,
        source: e.source_id,
        target: e.target_id,
        label: t(`anatomy.relations.${e.relation_type}`),
        labelStyle: { fontSize: 9, fill: '#6b7280' },
        labelBgStyle: { fill: '#f9fafb' },
        style: { stroke: '#94a3b8', strokeWidth: 1.5, opacity: dimmed ? 0.12 : 1 },
        animated: !dimmed,
      };
    });

    return { flowNodes, flowEdges };
  }, [nodes, edges, rootId, selectedNodeId, hiddenSet, t]);

  const onNodeClick = useCallback(
    (_e: React.MouseEvent, node: Node) => onSelectNode(node.id),
    [onSelectNode]
  );

  const onNodeDoubleClick = useCallback(
    (_e: React.MouseEvent, node: Node) => {
      if (node.id !== rootId) onFocusNode?.(node.id);
    },
    [onFocusNode, rootId]
  );

  if (nodes.length === 0) {
    return (
      <div className={`w-full h-full flex items-center justify-center ${className}`}>
        <p className="text-sm text-gray-300">{t('anatomy.graph_empty')}</p>
      </div>
    );
  }

  return (
    <div className={`w-full h-full ${className}`}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1.4 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.25}
        maxZoom={2}
      >
        <Background variant={BackgroundVariant.Dots} gap={12} size={1} color="#cbd5e1" />
        <Controls
          showInteractive={false}
          className="!bg-white dark:!bg-dark-surface !border-gray-200 dark:!border-dark-border"
        />
        <MiniMap
          nodeColor={(node) => {
            const cat = (Object.keys(CATEGORY_COLORS) as AnatomyCategory[]).find(
              (k) => CATEGORY_COLORS[k] === (node.style?.background as string)
            );
            return cat ? CATEGORY_COLORS[cat] : '#94a3b8';
          }}
          className="!bg-gray-50 dark:!bg-dark-bg !border-gray-200 dark:!border-dark-border"
          pannable
          zoomable
        />
      </ReactFlow>
    </div>
  );
};
