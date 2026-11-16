/**
 * App shim over the family ``concept-graph`` module (plan 60 Phase 5).
 *
 * The generic ReactFlow renderer — force/hierarchy layout, minimap, search,
 * PNG export, detail-card + context-menu render props, disabled/picked nodes —
 * lives in ``@neuronection/assistant-ui/concept-graph``. This module keeps
 * health's public surface stable for the catalog graph consumers:
 *
 * - the ``KIND_COLORS`` / ``RELATION_COLORS`` palettes (imported by the
 *   ontology, relations and picker graphs and by the relation filter), injected
 *   as the module defaults so call sites pass nothing;
 * - the two animated relations health has always animated;
 * - the permissive registry icon shape, narrowed at the library boundary.
 */
import React from 'react';
import {
  ConceptGraphView as LibraryConceptGraphView,
  type ConceptGraphEdgeData,
  type ConceptGraphIcon,
  type ConceptGraphNode as LibraryConceptGraphNode,
  type ConceptGraphViewProps as LibraryConceptGraphViewProps,
  type GraphNodeSummary,
  type RenderContextMenu,
  type RenderNodeDetail,
} from '@neuronection/assistant-ui/concept-graph';

export type {
  ConceptGraphEdgeData,
  ConceptGraphIcon,
  GraphNodeSummary,
  RenderContextMenu,
  RenderNodeDetail,
};

/**
 * Node fill color per concept kind (plus ``anatomy`` for the anatomy-structure
 * endpoints the ontology graph can surface). Consumed app-side for chips and
 * badges too, so it stays exported here.
 */
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

/** Edge stroke color per relation type (also feeds the legend's edge column). */
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

/** Relations the renderer animates — the diagnostic/procedural flow edges. */
export const ANIMATED_RELATIONS: readonly string[] = ['EXAMINES', 'PERFORMS'];

/**
 * A graph node. The catalog registry exposes its icon as
 * ``{ type: string; value: string }`` (the ``custom_svg`` / ``lucide`` union
 * is not narrowed server-side), so the app-facing shape stays permissive and
 * the shim narrows it at the library boundary.
 */
export type ConceptGraphNode = Omit<LibraryConceptGraphNode, 'icon'> & {
  icon?: { type: string; value: string } | null;
};

export interface ConceptGraphViewProps
  extends Omit<
    LibraryConceptGraphViewProps,
    'nodes' | 'kindColors' | 'relationColors' | 'animatedRelations'
  > {
  nodes: ConceptGraphNode[];
  /** Defaults to {@link KIND_COLORS}. */
  kindColors?: Record<string, string>;
  /** Defaults to {@link RELATION_COLORS}. */
  relationColors?: Record<string, string>;
  /** Defaults to {@link ANIMATED_RELATIONS}. */
  animatedRelations?: readonly string[];
}

export const ConceptGraphView = React.forwardRef<
  HTMLDivElement,
  ConceptGraphViewProps
>(function ConceptGraphView(
  {
    nodes,
    kindColors = KIND_COLORS,
    relationColors = RELATION_COLORS,
    animatedRelations = ANIMATED_RELATIONS,
    ...rest
  },
  ref,
) {
  return (
    <LibraryConceptGraphView
      ref={ref}
      {...rest}
      // Widened at the boundary: the registry's `type` column is a free-form
      // string, and the library's DynamicIcon falls back to a help icon for
      // anything that is neither 'lucide' nor 'custom_svg'.
      nodes={nodes as LibraryConceptGraphNode[]}
      kindColors={kindColors}
      relationColors={relationColors}
      animatedRelations={animatedRelations}
    />
  );
});
