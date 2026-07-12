/**
 * Hierarchy (dagre) layout helpers for the concept graph.
 *
 * In a hierarchical layout, nodes are arranged top-to-bottom based on edge
 * relationships. Different relation types have different semantic directions:
 *
 * - **Normal** (source above target): ``AFFECTS``, ``TREATS``, ``PREVENTS``,
 *   ``EXAMINES``, ``MONITORS``, … — the actor/influencer ranks higher.
 *   E.g. "biomarker A AFFECTS disease B" → A above B.
 *
 * - **Reversed** (target above source): ``MEMBER_OF``, ``PART_OF``,
 *   ``LOCATED_IN``, ``CLASSIFIED_AS``, ``HAS_SPECIALTY`` — the
 *   container/group/category ranks higher.
 *   E.g. "biomarker MEMBER_OF panel" → panel above biomarker.
 */

/**
 * Relations where the semantic hierarchy puts the TARGET above the SOURCE.
 * All other relations keep the source above the target (normal dagre TB).
 */
export const HIERARCHY_REVERSED_RELATIONS = new Set<string>([
  'MEMBER_OF',
  'PART_OF',
  'LOCATED_IN',
  'CLASSIFIED_AS',
  'HAS_SPECIALTY',
]);

/**
 * Given an edge's source, target, and relation type, returns the
 * `{ from, to }` direction to feed into dagre so the hierarchy respects
 * the semantic flow of each relation type.
 */
export function getHierarchyEdgeDirection(
  source: string,
  target: string,
  relation: string,
): { from: string; to: string } {
  if (HIERARCHY_REVERSED_RELATIONS.has(relation)) {
    return { from: target, to: source };
  }
  return { from: source, to: target };
}
