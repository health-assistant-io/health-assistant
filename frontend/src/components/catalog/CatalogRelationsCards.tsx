/**
 * Relations "cards" view (Phase E) — a structured list of a catalog item's
 * `concept_edges`, grouped by relation type. The counterpart to the graph
 * view; each card is clickable → navigates to the connected item.
 *
 * Pure presentational component: the parent (`CatalogRelationsGraph`) fetches
 * the data and owns the Graph/Cards toggle.
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight } from 'lucide-react';
import type { CatalogRelationResponse } from '../../types/catalog';

interface EndpointIndex {
  [id: string]: { label: string; type: string; kind?: string | null };
}

interface CatalogRelationsCardsProps {
  data: CatalogRelationResponse;
  /** Called when a card's destination endpoint is clicked. */
  onSelectEndpoint?: (type: string, id: string) => void;
}

export const CatalogRelationsCards: React.FC<CatalogRelationsCardsProps> = ({
  data,
  onSelectEndpoint,
}) => {
  const { t } = useTranslation();

  const endpointIndex = useMemo<EndpointIndex>(() => {
    const idx: EndpointIndex = {};
    for (const n of data.nodes || []) {
      idx[n.id] = {
        label: n.label || `${n.type}:${n.id.slice(0, 8)}`,
        type: n.type,
        kind: n.kind,
      };
    }
    return idx;
  }, [data]);

  // Group edges by relation type (TREATS, PREVENTS, …).
  const grouped = useMemo(() => {
    const acc: Record<string, typeof data.edges> = {};
    for (const e of data.edges || []) {
      (acc[e.relation] ??= []).push(e);
    }
    return acc;
  }, [data]);

  const relationTypes = Object.keys(grouped).sort();

  if (relationTypes.length === 0) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
        {t('catalogs.no_relations', 'No relations found.')}
      </p>
    );
  }

  const labelOf = (id: string) =>
    endpointIndex[id]?.label ?? `${id.slice(0, 8)}`;

  return (
    <div className="space-y-5">
      {relationTypes.map((relation) => (
        <div key={relation}>
          <p className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">
            {relation.replace(/_/g, ' ')}{' '}
            <span className="text-gray-400 font-normal">
              ({grouped[relation].length})
            </span>
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {grouped[relation].map((e) => {
              const dst = endpointIndex[e.dst.id];
              const dstType = dst?.type ?? e.dst.type;
              return (
                <button
                  key={e.id}
                  onClick={() => onSelectEndpoint?.(dstType, e.dst.id)}
                  className="group flex items-center gap-3 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left hover:border-blue-300 hover:bg-blue-50/50 dark:hover:bg-blue-900/10 transition-colors"
                  title={t('catalogs.open_relation', 'Open in catalog')}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {labelOf(e.dst.id)}
                    </p>
                    <p className="text-[11px] text-gray-400 capitalize">
                      {dstType}
                      {dst?.kind ? ` · ${dst.kind.replace(/_/g, ' ')}` : ''}
                    </p>
                  </div>
                  <ArrowRight className="w-4 h-4 text-gray-300 group-hover:text-blue-500 shrink-0" />
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
};
