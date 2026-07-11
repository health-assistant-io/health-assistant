/**
 * Relation-type (edge) filter for the graph — grouped, icon-rich chips that
 * mirror the relation editor's picker (``RelationTypeSelect``). Each chip
 * shows the relation's lucide icon, label, and the count of edges of that
 * relation in the current view. Toggling a chip hides/shows those edges
 * client-side.
 *
 * Pulls label/icon/group metadata from ``GET /catalogs/relation-types``
 * (cached session-wide) with a fallback to the bundled
 * ``RELATION_OPTION_GROUPS``. Only renders relation types that actually
 * appear in the supplied edges (keeps the row compact).
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DynamicIcon } from '../ui/DynamicIcon';
import { RELATION_COLORS } from '../ui/ConceptGraphView';
import {
  loadRelationTypes,
  getRelationTypes,
} from '../../services/catalogService';
import { RELATION_OPTION_GROUPS } from './catalogRelationTypes';
import type { ConceptGraphEdgeData } from '../ui/ConceptGraphView';
import type { RelationTypeMeta } from '../../types/catalog';

interface GraphRelationFilterProps {
  edges: ConceptGraphEdgeData[];
  hidden: Set<string>;
  onToggle: (relation: string) => void;
}

interface RelationGroup {
  group: string;
  relations: Array<{
    value: string;
    label: string;
    icon: string;
    count: number;
  }>;
}

export const GraphRelationFilter: React.FC<GraphRelationFilterProps> = ({
  edges,
  hidden,
  onToggle,
}) => {
  const { t } = useTranslation();
  const [, setTick] = useState(0);

  // Kick off the one-shot metadata fetch (cached session-wide).
  useEffect(() => {
    let mounted = true;
    loadRelationTypes().then(() => {
      if (mounted) setTick((n) => n + 1);
    });
    return () => {
      mounted = false;
    };
  }, []);

  // Build { relationValue → meta } from backend (or fallback groups).
  const metaMap = useMemo(() => {
    const map = new Map<string, RelationTypeMeta>();
    for (const m of getRelationTypes()) map.set(m.value, m);
    // Fallback: synthesize minimal meta from the bundled groups.
    if (map.size === 0) {
      for (const g of RELATION_OPTION_GROUPS) {
        for (const v of g.values) {
          map.set(v, {
            value: v,
            label: v.replace(/_/g, ' '),
            group: g.group,
            description: '',
            icon: { type: 'lucide', value: 'Link2' },
          });
        }
      }
    }
    return map;
  }, []);

  const groups = useMemo<RelationGroup[]>(() => {
    // Count edges per relation (only those present).
    const counts = new Map<string, number>();
    for (const e of edges) {
      counts.set(e.relation, (counts.get(e.relation) ?? 0) + 1);
    }
    const present = [...counts.entries()].sort((a, b) => b[1] - a[1]);

    const bucket = new Map<string, RelationGroup>();
    for (const [value, count] of present) {
      const meta = metaMap.get(value);
      const group = meta?.group ?? 'Other';
      if (!bucket.has(group)) {
        bucket.set(group, { group, relations: [] });
      }
      bucket.get(group)!.relations.push({
        value,
        label: meta?.label ?? value.replace(/_/g, ' '),
        icon: meta?.icon?.value ?? 'Link2',
        count,
      });
    }
    return [...bucket.values()];
  }, [edges, metaMap]);

  if (groups.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
        {t('catalogs.graph_relations', 'Relations')}:
      </span>
      {groups.map((g) => (
        <div key={g.group} className="flex flex-wrap items-center gap-1">
          {g.relations.map((r) => {
            const isHidden = hidden.has(r.value);
            const color = RELATION_COLORS[r.value] || '#94a3b8';
            return (
              <button
                key={r.value}
                onClick={() => onToggle(r.value)}
                title={`${r.label}${isHidden ? ' (hidden)' : ''}`}
                className={`flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-bold rounded-full border transition-all ${
                  isHidden
                    ? 'border-gray-200 dark:border-gray-600 text-gray-300 dark:text-gray-600 opacity-40 line-through hover:opacity-70'
                    : 'text-white border-transparent hover:brightness-110'
                }`}
                style={!isHidden ? { backgroundColor: color } : undefined}
              >
                <DynamicIcon
                  icon={r.icon}
                  className="w-2.5 h-2.5"
                />
                {r.label}
                <span
                  className={`ml-0.5 px-1 rounded-full text-[9px] ${
                    isHidden
                      ? 'bg-gray-100 dark:bg-gray-700'
                      : 'bg-black/20'
                  }`}
                >
                  {r.count}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
};
