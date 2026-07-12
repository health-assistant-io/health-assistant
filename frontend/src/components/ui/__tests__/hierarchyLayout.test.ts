import { describe, it, expect } from 'vitest';
import { getHierarchyEdgeDirection, HIERARCHY_REVERSED_RELATIONS } from '../hierarchyLayout';

describe('HIERARCHY_REVERSED_RELATIONS', () => {
  it('includes structural/containment relations (target above source)', () => {
    expect(HIERARCHY_REVERSED_RELATIONS.has('MEMBER_OF')).toBe(true);
    expect(HIERARCHY_REVERSED_RELATIONS.has('PART_OF')).toBe(true);
    expect(HIERARCHY_REVERSED_RELATIONS.has('LOCATED_IN')).toBe(true);
    expect(HIERARCHY_REVERSED_RELATIONS.has('CLASSIFIED_AS')).toBe(true);
    expect(HIERARCHY_REVERSED_RELATIONS.has('HAS_SPECIALTY')).toBe(true);
  });

  it('does NOT reverse action/causal relations (source above target)', () => {
    expect(HIERARCHY_REVERSED_RELATIONS.has('AFFECTS')).toBe(false);
    expect(HIERARCHY_REVERSED_RELATIONS.has('TREATS')).toBe(false);
    expect(HIERARCHY_REVERSED_RELATIONS.has('PREVENTS')).toBe(false);
    expect(HIERARCHY_REVERSED_RELATIONS.has('EXAMINES')).toBe(false);
    expect(HIERARCHY_REVERSED_RELATIONS.has('MONITORS')).toBe(false);
  });
});

describe('getHierarchyEdgeDirection', () => {
  it('keeps source→target for AFFECTS (source above target)', () => {
    const { from, to } = getHierarchyEdgeDirection('a', 'b', 'AFFECTS');
    expect(from).toBe('a');
    expect(to).toBe('b');
  });

  it('reverses for MEMBER_OF (group/target above member/source)', () => {
    // biomarker MEMBER_OF panel → panel should be above biomarker
    const { from, to } = getHierarchyEdgeDirection('biomarker', 'panel', 'MEMBER_OF');
    expect(from).toBe('panel');
    expect(to).toBe('biomarker');
  });

  it('reverses for PART_OF (whole above part)', () => {
    const { from, to } = getHierarchyEdgeDirection('organ', 'bodySystem', 'PART_OF');
    expect(from).toBe('bodySystem');
    expect(to).toBe('organ');
  });

  it('reverses for LOCATED_IN (container above contained)', () => {
    const { from, to } = getHierarchyEdgeDirection('thyroid', 'neck', 'LOCATED_IN');
    expect(from).toBe('neck');
    expect(to).toBe('thyroid');
  });

  it('keeps source→target for TREATS (treatment above disease)', () => {
    const { from, to } = getHierarchyEdgeDirection('med', 'disease', 'TREATS');
    expect(from).toBe('med');
    expect(to).toBe('disease');
  });

  it('keeps source→target for unknown relations (default normal)', () => {
    const { from, to } = getHierarchyEdgeDirection('x', 'y', 'UNKNOWN_RELATION');
    expect(from).toBe('x');
    expect(to).toBe('y');
  });
});
