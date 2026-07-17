/**
 * ObservationView — the per-type browse view for biomarker-result instances.
 *
 * Reuses the canonical biomarker normalization (`useBiomarkers`) and rendering
 * helpers (`getFinalStatus`, `getStatusColorClass`, `formatBiomarkerValue`)
 * from the analytics/trends surface — so observations display as proper
 * biomarker cards (name, value + unit, status, reference range, date), not as
 * generic uniform rows. A card grid (no preview pane), matching the trends
 * page's "cards only" presentation.
 *
 * Pick affordance: each card carries an Add/Added toggle bound to the raw
 * observation (the picker operates on observation ids). Registered for the
 * `observation` type via the view registry.
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Check, Activity } from 'lucide-react';
import { useBiomarkers } from '../../../hooks/useBiomarkers';
import { FilterBar } from '../../../components/ui/filters/FilterBar';
import { useFilterState } from '../../../components/ui/filters';
import { BiomarkerResultCard } from '../../../components/biomarkers/BiomarkerResultCard';
import { trendsBiomarkerFacets } from '../../biomarkers/facets/trendsFacets';
import type { InstanceViewProps } from '../../../components/instances/types';
import type { Observation } from '../../../types/observation';
import type { BiomarkerObservation } from '../../../types/biomarker';

export const ObservationView: React.FC<InstanceViewProps<Observation>> = ({
  items,
  pickedIds,
  onTogglePick,
  loading,
}) => {
  const { t } = useTranslation();
  // Enrich raw observations → BiomarkerObservation (the shape that carries
  // isTelemetry / techCategory / labName), then filter with the SAME facets the
  // /analytics/trends page uses — single source of truth for biomarker filters
  // (status / source_type / subcategory / unit / source / mapped).
  const { biomarkers } = useBiomarkers({ observations: items });
  const filter = useFilterState<BiomarkerObservation>(trendsBiomarkerFacets);
  const filtered = useMemo(
    () => filter.applyFilters(biomarkers),
    [filter, biomarkers],
  );

  // Map observation id -> raw observation so the pick toggle can hand back the
  // raw item (the picker operates on the raw entity, not the transformed one).
  const byId = useMemo(() => {
    const m = new Map<string, Observation>();
    for (const o of items) m.set(o.id, o);
    return m;
  }, [items]);

  if (loading && biomarkers.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        {t('common.loading', 'Loading…')}
      </div>
    );
  }

  if (biomarkers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center">
        <Activity className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
        <p className="text-sm text-gray-400">
          {t('instances.no_matches', 'No matches')}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full gap-2 min-h-0">
      <FilterBar
        facets={trendsBiomarkerFacets}
        filter={filter}
        items={biomarkers}
        showActivePills
        resultCount={filtered.length}
        totalCount={biomarkers.length}
      />
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Activity className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
            <p className="text-sm text-gray-400">
              {t('instances.no_matches', 'No matches')}
            </p>
          </div>
        ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 pr-1">
          {filtered.map((b) => {
          const isPicked = pickedIds.includes(b.id);
          const raw = byId.get(b.id);
          return (
            <div
              key={b.id}
              className={`relative rounded-2xl border p-4 transition-all ${
                isPicked
                  ? 'border-green-400 bg-green-50/40 dark:bg-green-900/10'
                  : 'border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface hover:border-blue-300'
              }`}
            >
              <button
                type="button"
                onClick={() => raw && onTogglePick(raw)}
                className={`absolute top-3 right-3 inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
                  isPicked
                    ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                    : 'bg-blue-600 text-white hover:bg-blue-700'
                }`}
              >
                {isPicked ? (
                  <>
                    <Check className="w-3 h-3" /> {t('instances.picker_added', 'Added')}
                  </>
                ) : (
                  <>
                    <Plus className="w-3 h-3" /> {t('common.add', 'Add')}
                  </>
                )}
              </button>

              <div className="pr-16">
                <BiomarkerResultCard b={b} />
              </div>
            </div>
          );
        })}
        </div>
        )}
      </div>
    </div>
  );
};
