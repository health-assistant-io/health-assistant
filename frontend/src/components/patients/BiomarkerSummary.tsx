import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, ArrowUpRight, RefreshCw, AlertTriangle, Upload } from 'lucide-react';
import { format, parseISO } from 'date-fns';
import { getBiomarkerTrends, getCachedBiomarkerTrends } from '../../services/analyticsService';
import { useBiomarkers } from '../../hooks/useBiomarkers';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useBiomarkerPrecisionProfile } from '../../hooks/useBiomarkerPrecision';
import { BiomarkerObservation } from '../../types/biomarker';
import { isAbnormal, formatUnit, formatBiomarkerValue } from '../../utils/biomarkerUtils';
import LineChart from '../charts/LineChart';
import SummaryCardHeader, { TAG_NEUTRAL, TAG_RED } from '../ui/SummaryCardHeader';

interface Props {
  patientId: string;
}

const SPARKLINE_PERIOD = 'last-30-days' as const;
const SPARKLINE_AGGREGATION = '1 day';
const MAX_TILES = 4;
const SPARKLINE_POINTS = 10;

const BiomarkerSummary: React.FC<Props> = ({ patientId }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore(state => state.user);
  const showReferenceRanges = useSettingsStore(state => state.showReferenceRanges);
  const precisionProfile = useBiomarkerPrecisionProfile();

  const [trendsData, setTrendsData] = useState<Record<string, any[]> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const loadingRef = useRef(false);

  const { biomarkers, getAbnormal } = useBiomarkers({ trendsData: trendsData || undefined });

  const fetchTrends = async () => {
    if (!patientId || loadingRef.current) return;
    loadingRef.current = true;
    setError(false);

    try {
      // Cache-first for instant paint
      const cached = await getCachedBiomarkerTrends(patientId, SPARKLINE_PERIOD).catch(() => null);
      if (cached) {
        setTrendsData(cached.biomarkers ?? null);
        setLoading(false);
      } else {
        setLoading(true);
      }

      // Fresh network fetch
      const fresh = await getBiomarkerTrends(
        user?.tenant_id ?? '',
        '',
        SPARKLINE_PERIOD,
        patientId,
        SPARKLINE_AGGREGATION
      );
      setTrendsData(fresh.biomarkers ?? {});
    } catch (err) {
      console.error('Failed to fetch biomarker trends for summary card', err);
      if (!trendsData) setError(true);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  };

  useEffect(() => {
    fetchTrends();
  }, [patientId]);

  const abnormalCount = getAbnormal().length;
  const trackedCount = biomarkers.length;

  // Abnormal-first selection: most-severe abnormals, then most-recent normals
  const selectedTiles = useMemo<BiomarkerObservation[]>(() => {
    if (biomarkers.length === 0) return [];

    const severityScore = (b: BiomarkerObservation): number => {
      const val = b.value.normalized ?? b.value.raw;
      const min = b.referenceRange.min;
      const max = b.referenceRange.max;
      if (min == null && max == null) return 0;
      const range = Math.abs((max ?? 0) - (min ?? 0));
      const safeRange = range > 0 ? range : Math.abs(max ?? min ?? 1);
      if (max != null && val > max) return (val - max) / safeRange;
      if (min != null && val < min) return (min - val) / safeRange;
      return 0;
    };

    const recencyScore = (b: BiomarkerObservation): number => {
      const d = b.source?.date;
      return d ? new Date(d).getTime() : 0;
    };

    const abnormal = biomarkers
      .filter(b => isAbnormal(b.interpretation))
      .sort((a, b) => severityScore(b) - severityScore(a));

    const normal = biomarkers
      .filter(b => !isAbnormal(b.interpretation))
      .sort((a, b) => recencyScore(b) - recencyScore(a));

    return [...abnormal, ...normal].slice(0, MAX_TILES);
  }, [biomarkers]);

  const toSparklineData = (obs: BiomarkerObservation) => {
    const history: any[] = obs._rawJson?.history ?? [];
    return history.slice(-SPARKLINE_POINTS).map(p => ({
      name: p.date ? format(parseISO(p.date), 'MMM d') : '',
      value: p.value,
    }));
  };

  const tileColor = (interpretation: string): string => {
    const s = interpretation.toLowerCase();
    if (s.includes('high') || s === 'h' || s.includes('abnormal')) return '#ef4444';
    if (s.includes('low') || s === 'l') return '#3b82f6';
    return '#10b981';
  };

  const valueColorClass = (interpretation: string): string => {
    const s = interpretation.toLowerCase();
    if (s.includes('high') || s.includes('abnormal')) return 'text-red-600 dark:text-red-400';
    if (s.includes('low')) return 'text-blue-600 dark:text-blue-400';
    return 'text-gray-900 dark:text-dark-text';
  };

  const handleTileClick = (obs: BiomarkerObservation) => {
    if (obs.definitionId) {
      navigate(`/biomarkers/details/${obs.definitionId}`);
    } else if (obs.slug) {
      navigate(`/biomarkers/${obs.slug}`);
    }
  };

  if (loading) {
    return (
      <div className="animate-pulse bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border w-full h-full">
        <div className="h-4 w-32 bg-gray-200 rounded mb-4" />
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-20 bg-gray-50 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full flex flex-col">
        <SummaryCardHeader
          icon={Activity}
          iconClassName="text-gray-400"
          title={t('common.biomarkers')}
          info={{
            title: t('common.biomarkers'),
            content: t('biomarkers.info_text'),
            ariaLabel: t('common.info'),
          }}
          titleTo="/analytics/trends"
        />
        <div className="p-6 flex flex-col items-center justify-center text-center">
          <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
          <p className="text-sm text-gray-500 dark:text-dark-muted mb-3">{t('common.error')}</p>
          <button
            onClick={fetchTrends}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold"
          >
            <RefreshCw className="w-3 h-3" />
            <span>{t('common.retry')}</span>
          </button>
        </div>
      </div>
    );
  }

  const tags = [
    <span key="tracked" className={TAG_NEUTRAL}>{trackedCount} {t('biomarkers.tracked')}</span>,
    abnormalCount > 0 && (
      <span key="abnormal" className={TAG_RED}>{abnormalCount} {t('biomarkers.out_of_range')}</span>
    ),
  ].filter(Boolean) as React.ReactNode[];

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full flex flex-col">
      <SummaryCardHeader
        icon={Activity}
        iconClassName="text-blue-500"
        title={t('common.biomarkers')}
        info={{
          title: t('common.biomarkers'),
          content: t('biomarkers.info_text'),
          ariaLabel: t('common.info'),
        }}
          tags={tags}
          titleTo="/analytics/trends"
        />

      {/* Body */}
      <div className="p-4 sm:p-6 flex-1">
        {trackedCount === 0 ? (
          <div className="flex flex-col items-center justify-center text-center py-6">
            <Activity className="w-10 h-10 text-gray-200 dark:text-dark-border mb-2" />
            <p className="text-sm text-gray-400 dark:text-dark-muted italic mb-3">{t('biomarkers.no_trends_yet')}</p>
            <button
              onClick={() => navigate('/examinations/upload')}
              className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold"
            >
              <Upload className="w-3 h-3" />
              <span>{t('common.new_examination')}</span>
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {selectedTiles.map(obs => {
              const data = toSparklineData(obs);
              const isClickable = !!(obs.definitionId || obs.slug);
              const color = tileColor(obs.interpretation);
              const range = obs.referenceRange;

              return (
                <button
                  key={obs.id}
                  type="button"
                  onClick={() => handleTileClick(obs)}
                  disabled={!isClickable}
                  aria-label={`${t('biomarkers.trend_for', { name: obs.displayName })}: ${formatBiomarkerValue(obs.value.normalized ?? obs.value.raw, precisionProfile)} ${obs.unit?.normalizedSymbol || obs.unit?.rawSymbol || ''}`}
                  className={`relative text-left rounded-xl border p-3 transition-all bg-gray-50/50 dark:bg-dark-bg/30 border-gray-100 dark:border-dark-border ${isClickable ? 'hover:border-blue-200 dark:hover:border-blue-900 hover:shadow-sm cursor-pointer' : 'cursor-default'}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <p className="text-[10px] font-black text-gray-500 dark:text-dark-muted uppercase tracking-widest truncate leading-tight">
                      {obs.displayName}
                    </p>
                    {isAbnormal(obs.interpretation) && (
                      <span className={`shrink-0 inline-block w-1.5 h-1.5 rounded-full ${obs.interpretation.toLowerCase().includes('high') ? 'bg-red-500' : 'bg-blue-500'}`} />
                    )}
                  </div>

                  <div className="flex items-baseline space-x-1 mb-1">
                    <span className={`text-lg font-black tracking-tighter leading-none ${valueColorClass(obs.interpretation)}`}>
                      {formatBiomarkerValue(obs.value.normalized ?? obs.value.raw, precisionProfile)}
                    </span>
                    {(obs.unit?.normalizedSymbol || obs.unit?.rawSymbol) && (
                      <span className="text-[9px] font-bold text-gray-400 dark:text-dark-muted uppercase">
                        {formatUnit(obs.unit?.normalizedSymbol || obs.unit?.rawSymbol || '')}
                      </span>
                    )}
                  </div>

                  {data.length > 1 ? (
                    <div className="h-8 w-full nodrag">
                      <LineChart
                        data={data}
                        height="100%"
                        color={color}
                        chartType="line"
                        showGrid={false}
                        showReferenceLines={false}
                        showBrush={false}
                        strokeWidth={2}
                        hideAxes
                        hideTooltip={false}
                        unit={obs.unit?.normalizedSymbol || obs.unit?.rawSymbol || ''}
                        referenceRange={showReferenceRanges && range ? { min: range.min, max: range.max } : undefined}
                      />
                    </div>
                  ) : (
                    <div className="h-8 flex items-center text-[9px] text-gray-400 dark:text-dark-muted italic">
                      {t('biomarkers.single_reading')}
                    </div>
                  )}
                </button>
              );
            })}

            {/* Fill empty slots with a "view all" prompt if we have fewer than 4 tiles */}
            {trackedCount > MAX_TILES && (
              <button
                onClick={() => navigate('/analytics/trends')}
                title={t('common.open_x', { x: t('common.biomarker_trends') })}
                className="rounded-xl border border-dashed border-gray-200 dark:border-dark-border p-3 flex flex-col items-center justify-center text-center hover:border-blue-300 hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-all group"
              >
                <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-0.5">
                  +{trackedCount - MAX_TILES}
                </span>
                <ArrowUpRight className="w-3 h-3 text-blue-600 dark:text-blue-400 group-hover:translate-x-0.5 transition-transform" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default React.memo(BiomarkerSummary);
