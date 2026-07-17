/**
 * ObservationDetail — the single-record detail view for an observation
 * instance, shown in `InstanceCard`'s "open" overlay.
 *
 * A mapped observation resolves to its biomarker definition and renders the
 * SAME rich tabs the BiomarkerDetail page uses — {@link BiomarkerTrendTab}
 * (longitudinal chart + time-range/aggregation toolbar), {@link BiomarkerInfoTab}
 * (clinical significance), and {@link BiomarkerHistoryTab} (observations table)
 * — via the shared {@link useBiomarkerDetailData} hook. So the overlay is as
 * rich as the biomarker trends/analytics pages, not a bare single-value card.
 *
 * An unmapped observation (no definition) falls back to the shared
 * {@link BiomarkerResultCard} (value + unit + status + reference range).
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TrendingUp, Info, Calendar, Activity, AlertTriangle } from 'lucide-react';
import {
  BiomarkerInfoTab,
  BiomarkerHistoryTab,
  BiomarkerTrendTab,
} from '../../../components/biomarkers/tabs';
import { BiomarkerResultCard } from '../../../components/biomarkers/BiomarkerResultCard';
import { BiomarkerKpiStrip } from '../../../components/biomarkers/BiomarkerKpiStrip';
import { LoadingState } from '../../../components/ui/LoadingState';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';
import { useBiomarkers } from '../../../hooks/useBiomarkers';
import { useBiomarkerDetailData } from '../../../hooks/useBiomarkerDetailData';
import { getObservation } from '../../../services/observationService';
import type { InstanceDetailProps } from '../../../components/instances/detailViewRegistry';
import type { Observation } from '../../../types/observation';

type TabId = 'trend' | 'info' | 'history';

export const ObservationDetail: React.FC<InstanceDetailProps> = ({ id, patientId }) => {
  const { t } = useTranslation();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const [observation, setObservation] = useState<Observation | null>(null);
  const [obsLoading, setObsLoading] = useState(true);
  const [obsError, setObsError] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>('trend');

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setObsLoading(true);
      setObsError(false);
      try {
        const obs = await getObservation(id);
        if (!cancelled) setObservation(obs);
      } catch {
        if (!cancelled) {
          setObsError(true);
          setObservation(null);
        }
      } finally {
        if (!cancelled) setObsLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Mapped path: resolve the biomarker definition + its trend (rich tabs).
  const {
    biomarker,
    trends,
    filteredTrends,
    loading: bioLoading,
    dateRange,
    setDateRange,
    aggregation,
    setAggregation,
  } = useBiomarkerDetailData(observation?.biomarker_id, patientId);

  // Unmapped fallback: normalize the single observation into a value card.
  // Hook must run unconditionally; it no-ops when observation is null.
  const { biomarkers } = useBiomarkers({
    observations: observation && !observation.biomarker_id ? [observation] : [],
  });

  if (obsLoading || bioLoading) {
    return <LoadingState variant="section" />;
  }

  if (obsError) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-center">
        <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('instances.card_unavailable', 'Record unavailable')}
        </p>
      </div>
    );
  }

  // Mapped → rich biomarker tabs (the single source of truth the detail page uses).
  if (biomarker) {
    const TABS: { id: TabId; label: string; icon: typeof TrendingUp }[] = [
      { id: 'trend', label: t('biomarkers.tab_trend', 'Trend'), icon: TrendingUp },
      { id: 'info', label: t('biomarkers.tab_clinical', 'Clinical'), icon: Info },
      { id: 'history', label: t('biomarkers.tab_history', 'History'), icon: Calendar },
    ];
    return (
      <div>
        <div className="px-6 pt-6 pb-4">
          <BiomarkerKpiStrip
            biomarker={biomarker}
            trends={trends}
            precisionProfile={precisionProfile}
          />
        </div>
        <div className="px-6 pt-5 pb-3 border-t border-gray-100 dark:border-dark-border sticky top-0 bg-white dark:bg-dark-surface z-10">
          <div className="flex flex-wrap items-center gap-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl w-fit max-w-full">
            {TABS.map((tabItem) => {
              const Icon = tabItem.icon;
              return (
                <button
                  key={tabItem.id}
                  type="button"
                  onClick={() => setActiveTab(tabItem.id)}
                  className={`flex items-center space-x-1.5 px-4 py-2 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${
                    activeTab === tabItem.id
                      ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm'
                      : 'text-gray-400 hover:text-gray-600'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  <span>{tabItem.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {activeTab === 'trend' && (
          <BiomarkerTrendTab
            biomarker={biomarker}
            filteredTrends={filteredTrends}
            dateRange={dateRange}
            setDateRange={setDateRange}
            aggregation={aggregation}
            setAggregation={setAggregation}
          />
        )}
        {activeTab === 'info' && <BiomarkerInfoTab biomarker={biomarker} />}
        {activeTab === 'history' && (
          <BiomarkerHistoryTab
            biomarker={biomarker}
            filteredTrends={filteredTrends}
            precisionProfile={precisionProfile}
          />
        )}
      </div>
    );
  }

  // Unmapped → value-only card (still better than the empty generic preview).
  if (biomarkers.length > 0) {
    return (
      <div className="p-6 space-y-3">
        {biomarkers.map((b) => (
          <div
            key={b.id}
            className="rounded-2xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface p-4"
          >
            <BiomarkerResultCard b={b} />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center py-10 text-center">
      <Activity className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
      <p className="text-sm text-gray-500 dark:text-dark-muted">
        {t('instances.no_matches', 'No matches')}
      </p>
    </div>
  );
};
