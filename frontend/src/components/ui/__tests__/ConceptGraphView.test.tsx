import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import {
  DEFAULT_HIERARCHY_REVERSED_RELATIONS,
  getHierarchyEdgeDirection,
} from '@neuronection/assistant-ui/concept-graph';

import {
  ANIMATED_RELATIONS,
  ConceptGraphView,
  KIND_COLORS,
  RELATION_COLORS,
  type ConceptGraphEdgeData,
  type ConceptGraphNode,
} from '../ConceptGraphView';

const captured = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }));

// The library renderer mounts ReactFlow (ResizeObserver + measured nodes);
// the shim's contract is the props it hands over, so stand in a recorder.
vi.mock('@neuronection/assistant-ui/concept-graph', async importOriginal => {
  const actual = await importOriginal<typeof import('@neuronection/assistant-ui/concept-graph')>();
  const react = await import('react');
  const Recorder = react.forwardRef<HTMLDivElement, Record<string, unknown>>((props, ref) => {
    captured.props = props;
    return react.createElement('div', { 'data-testid': 'library-concept-graph', ref });
  });
  return { ...actual, ConceptGraphView: Recorder };
});

const NODES: ConceptGraphNode[] = [
  { id: 'n1', name: 'Hypertension', primary_kind: 'disease' },
  { id: 'n2', name: 'Systolic pressure', primary_kind: 'biomarker_panel' },
];
const EDGES: ConceptGraphEdgeData[] = [
  { id: 'e1', source: 'n2', target: 'n1', relation: 'INDICATES' },
];

function renderGraph(props: Partial<React.ComponentProps<typeof ConceptGraphView>> = {}) {
  return render(<ConceptGraphView nodes={NODES} edges={EDGES} {...props} />);
}

describe('ConceptGraphView shim', () => {
  beforeEach(() => {
    captured.props = null;
  });

  it('renders through the family concept-graph module', () => {
    renderGraph();
    expect(captured.props).not.toBeNull();
  });

  it('injects the health palettes and animated relations as defaults', () => {
    renderGraph();

    expect(captured.props?.kindColors).toBe(KIND_COLORS);
    expect(captured.props?.relationColors).toBe(RELATION_COLORS);
    expect(captured.props?.animatedRelations).toBe(ANIMATED_RELATIONS);
  });

  it('lets a caller override the injected defaults (e.g. a picker palette)', () => {
    const kindColors = { disease: '#000000' };
    const relationColors = { INDICATES: '#111111' };

    renderGraph({ kindColors, relationColors, animatedRelations: [] });

    expect(captured.props?.kindColors).toBe(kindColors);
    expect(captured.props?.relationColors).toBe(relationColors);
    expect(captured.props?.animatedRelations).toEqual([]);
  });

  it('forwards picker selection sets, render props and callbacks unchanged', () => {
    const disabledNodeIds = new Set(['n2']);
    const pickedNodeIds = new Set(['n1']);
    const onSelectNode = vi.fn();
    const onFocusNode = vi.fn();
    const onClearSelection = vi.fn();
    const renderNodeDetail = () => null;
    const renderContextMenu = () => null;

    renderGraph({
      disabledNodeIds,
      pickedNodeIds,
      onSelectNode,
      onFocusNode,
      onClearSelection,
      renderNodeDetail,
      renderContextMenu,
      selectedNodeId: 'n1',
      hiddenKinds: ['organ'],
      showMiniMap: false,
      className: 'h-[400px]',
    });

    expect(captured.props?.nodes).toBe(NODES);
    expect(captured.props?.edges).toBe(EDGES);
    expect(captured.props?.disabledNodeIds).toBe(disabledNodeIds);
    expect(captured.props?.pickedNodeIds).toBe(pickedNodeIds);
    expect(captured.props?.onSelectNode).toBe(onSelectNode);
    expect(captured.props?.onFocusNode).toBe(onFocusNode);
    expect(captured.props?.onClearSelection).toBe(onClearSelection);
    expect(captured.props?.renderNodeDetail).toBe(renderNodeDetail);
    expect(captured.props?.renderContextMenu).toBe(renderContextMenu);
    expect(captured.props?.selectedNodeId).toBe('n1');
    expect(captured.props?.hiddenKinds).toEqual(['organ']);
    expect(captured.props?.showMiniMap).toBe(false);
    expect(captured.props?.className).toBe('h-[400px]');
  });

  it('forwards a ref to the rendered graph element', () => {
    const ref = React.createRef<HTMLDivElement>();
    render(
      <ConceptGraphView ref={ref} nodes={NODES} edges={EDGES} />,
    );
    expect(ref.current).toBeTruthy();
  });
});

describe('health palettes', () => {
  it('keeps the concept-kind colors consumers import', () => {
    expect(KIND_COLORS.disease).toBe('#ef4444');
    expect(KIND_COLORS.biomarker_panel).toBe('#f59e0b');
    expect(KIND_COLORS.anatomy).toBe('#10b981');
  });

  it('keeps the relation colors consumers import', () => {
    expect(RELATION_COLORS.EXAMINES).toBe('#3b82f6');
    expect(RELATION_COLORS.MEMBER_OF).toBe('#8b5cf6');
    expect(RELATION_COLORS.CLASSIFIED_AS).toBe('#6b7280');
  });

  it('animates the diagnostic/procedural relations', () => {
    expect(ANIMATED_RELATIONS).toEqual(['EXAMINES', 'PERFORMS']);
  });
});

// The dagre hierarchy helper moved into the library with the graph itself
// (health's reversed set is the library default). These assertions pin the
// semantics health relies on, so a library default change is caught here.
describe('hierarchy layout parity (family default)', () => {
  it('reverses structural/containment relations', () => {
    for (const relation of ['MEMBER_OF', 'PART_OF', 'LOCATED_IN', 'CLASSIFIED_AS', 'HAS_SPECIALTY']) {
      expect(DEFAULT_HIERARCHY_REVERSED_RELATIONS).toContain(relation);
    }
  });

  it('does not reverse action/causal relations', () => {
    for (const relation of ['AFFECTS', 'TREATS', 'PREVENTS', 'EXAMINES', 'MONITORS']) {
      expect(DEFAULT_HIERARCHY_REVERSED_RELATIONS).not.toContain(relation);
    }
  });

  it('keeps source above target for TREATS', () => {
    expect(getHierarchyEdgeDirection('med', 'disease', 'TREATS')).toEqual({
      from: 'med',
      to: 'disease',
    });
  });

  it('puts the container/group above the member', () => {
    expect(getHierarchyEdgeDirection('biomarker', 'panel', 'MEMBER_OF')).toEqual({
      from: 'panel',
      to: 'biomarker',
    });
    expect(getHierarchyEdgeDirection('thyroid', 'neck', 'LOCATED_IN')).toEqual({
      from: 'neck',
      to: 'thyroid',
    });
  });

  it('keeps source above target for unknown relations', () => {
    expect(getHierarchyEdgeDirection('x', 'y', 'UNKNOWN_RELATION')).toEqual({
      from: 'x',
      to: 'y',
    });
  });
});
