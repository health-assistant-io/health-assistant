/**
 * Shared relation-type options for cross-catalog edges.
 *
 * Mirrors the backend `ConceptRelationType` enum
 * (`backend/app/models/enums.py:ConceptRelationType`). Keep these in sync
 * when a relation type is added/removed on the backend. Surfaced as the
 * default picker options for {@link CatalogItemPicker} (relation mode) and
 * consumed by {@link CatalogRelationsEditor}.
 */
export interface RelationOptionGroup {
  group: string;
  values: string[];
}

export const RELATION_OPTION_GROUPS: RelationOptionGroup[] = [
  {
    group: 'Medical knowledge',
    values: [
      'TREATS', 'PREVENTS', 'AFFECTS', 'INDICATES', 'CONTRAINDICATES',
      'CORRELATES_WITH', 'CAUSED_BY', 'MONITORS', 'RISK_OF', 'SCREENS_FOR',
    ],
  },
  {
    group: 'Structural / classification',
    values: [
      'MEMBER_OF', 'CLASSIFIED_AS', 'PART_OF', 'LOCATED_IN', 'HAS_SPECIALTY',
      'EXAMINES', 'IMAGES', 'PERFORMS', 'ORDERS',
    ],
  },
];

/** Flat list of every relation value across all groups. */
export const RELATION_VALUES: string[] = RELATION_OPTION_GROUPS.flatMap(
  (g) => g.values,
);

/** Sensible default when a caller doesn't pick a specific relation. */
export const DEFAULT_RELATION = 'AFFECTS';

/** Filter the full option-group list to a subset of relation values.
 *  Used by per-destination-type filtering (e.g. a medication→concept link
 *  shows only TREATS / CONTRAINDICATES / INDICATES / RISK_OF, not the full
 *  list). Empty groups are dropped so the dropdown stays compact. */
export function filterRelationGroups(
  groups: RelationOptionGroup[],
  allowed: readonly string[],
): RelationOptionGroup[] {
  const allow = new Set(allowed);
  return groups
    .map((g) => ({
      group: g.group,
      values: g.values.filter((v) => allow.has(v)),
    }))
    .filter((g) => g.values.length > 0);
}
