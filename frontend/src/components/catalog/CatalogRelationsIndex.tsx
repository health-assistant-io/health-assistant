/**
 * Catalog relations index — the Relations tab's "All items" view (Layer 2).
 *
 * When no single item is selected, the Relations tab shows the *relation
 * topology of the whole category*: every item that has outgoing edges, grouped
 * by relation type (TREATS / AFFECTS / PREVENTS / …). Each entry is clickable →
 * selects that item (`?item=`) and flips to its graph. Reuses the
 * `?include=relations` data the browser already fetched (no extra round-trip).
 *
 * This is deliberately not a duplicate of the name-sorted browser: it's the
 * relation-centric view of the catalog.
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { GitBranch, ArrowRight } from 'lucide-react';
import { ScopeBadge } from './ScopeBadge';
import type { CatalogItem } from '../../types/catalog';
import { useAuthStore } from '../../store/slices/authSlice';

interface CatalogRelationsIndexProps {
  items: CatalogItem[];
  onSelectItem: (id: string) => void;
}

export const CatalogRelationsIndex: React.FC<CatalogRelationsIndexProps> = ({
  items,
  onSelectItem,
}) => {
  const { t } = useTranslation();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);

  // relation type → [{ item, count }]
  const grouped = useMemo(() => {
    const acc: Record<string, Array<{ item: CatalogItem; count: number }>> = {};
    for (const it of items) {
      const breakdown = it.relation_breakdown;
      if (!breakdown) continue;
      for (const [relation, count] of Object.entries(breakdown)) {
        if (count > 0) (acc[relation] ??= []).push({ item: it, count });
      }
    }
    // Sort each group by count desc, then name.
    for (const r of Object.keys(acc)) {
      acc[r].sort((a, b) => b.count - a.count || String(a.item.name).localeCompare(String(b.item.name)));
    }
    return acc;
  }, [items]);

  const relationTypes = Object.keys(grouped).sort();
  const totalRelated = relationTypes.reduce((s, r) => s + grouped[r].length, 0);

  if (totalRelated === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 dark:border-gray-600 p-10 text-center">
        <GitBranch className="w-8 h-8 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {t('catalogs.relations_index_empty', 'No outgoing relations in this catalog yet.')}
        </p>
        <p className="text-xs text-gray-400 mt-1">
          {t('catalogs.relations_index_hint', 'Select an item to view and edit its relations.')}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <p className="text-xs text-gray-400">
        {t('catalogs.relations_index_help', {
          defaultValue: '{{count}} items with outgoing relations, grouped by type.',
          count: totalRelated,
        })}
      </p>
      {relationTypes.map((relation) => (
        <div key={relation}>
          <p className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">
            {relation.replace(/_/g, ' ')}{' '}
            <span className="text-gray-400 font-normal">
              ({grouped[relation].length})
            </span>
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {grouped[relation].map(({ item, count }) => (
              <button
                key={String(item.id)}
                onClick={() => onSelectItem(String(item.id))}
                className="group flex items-center gap-3 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left hover:border-blue-300 hover:bg-blue-50/50 dark:hover:bg-blue-900/10 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium truncate">
                      {String(item.name ?? item.slug ?? item.id)}
                    </p>
                    <ScopeBadge
                      scope={item.scope}
                      created_by={item.created_by}
                      currentUserId={currentUserId}
                    />
                  </div>
                  <p className="text-[11px] text-gray-400 mt-0.5">
                    {count} {t('catalogs.relation_count_suffix', 'edge(s)')}
                  </p>
                </div>
                <ArrowRight className="w-4 h-4 text-gray-300 group-hover:text-blue-500 shrink-0" />
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};
