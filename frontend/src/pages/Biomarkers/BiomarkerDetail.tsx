import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, Calendar, TrendingUp, Box, Share2, Printer, Database, Tag, Info } from 'lucide-react';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { formatUnit, getFinalStatus, getStatusColorClass, formatBiomarkerValue } from '../../utils/biomarkerUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { TabInfoButton } from '../../components/ui/TabInfoButton';
import biomarkerService from '../../services/biomarkerService';
import { getBiomarkerTrends } from '../../services/analyticsService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { useTabScroll } from '../../hooks/useTabScroll';
import { Biomarker } from '../../types/biomarker';
import { TimePeriod, DEFAULT_AGGREGATIONS, AggregationBucket, getCutoffDate } from '../../config/timeRanges';
import { MigrationProgressIndicator } from '../../components/biomarkers/MigrationProgressIndicator';
import { useBiomarkerPrecisionProfile } from '../../hooks/useBiomarkerPrecision';
import {
  BiomarkerInfoTab,
  BiomarkerHistoryTab,
  BiomarkerInsightsTab,
  BiomarkerRelationsTab,
  BiomarkerTechnicalTab,
  BiomarkerTrendTab,
} from '../../components/biomarkers/tabs';

type BiomarkerTabId = 'trend' | 'info' | 'history' | 'insights' | 'relations' | 'technical';
const VALID_TABS: BiomarkerTabId[] = ['trend', 'info', 'history', 'insights', 'relations', 'technical'];

const BiomarkerDetail: React.FC = () => {
  const { t } = useTranslation();
  const { biomarkerId, activeTab: routeTab } = useParams<{ biomarkerId: string; activeTab: string }>();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const precisionProfile = useBiomarkerPrecisionProfile();

  const decodedId = decodeURIComponent(biomarkerId || '');

  const setCurrentBiomarkerId = useUIStore(state => state.setCurrentBiomarkerId);

  const [biomarker, setBiomarker] = useState<Biomarker | null>(null);
  const [trends, setTrends] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  // URL-synced active tab (deep-linkable, matches ExaminationDetail convention).
  // Trend is the default — it's the primary view when opening a biomarker.
  const initialTab: BiomarkerTabId = VALID_TABS.includes(routeTab as BiomarkerTabId)
    ? (routeTab as BiomarkerTabId)
    : 'trend';
  const [activeTab, setActiveTab] = useState<BiomarkerTabId>(initialTab);
  const tabsRef = React.useRef<HTMLDivElement>(null);
  useTabScroll(tabsRef, activeTab);

  // Keep local tab in sync if the URL changes (back/forward navigation).
  useEffect(() => {
    if (routeTab && VALID_TABS.includes(routeTab as BiomarkerTabId)) {
      setActiveTab(routeTab as BiomarkerTabId);
    } else if (!routeTab) {
      setActiveTab('trend');
    }
  }, [routeTab]);

  const handleTabChange = (tab: BiomarkerTabId) => {
    setActiveTab(tab);
    navigate(`/biomarkers/details/${decodedId}/${tab}`, { replace: true });
  };

  const [dateRange, setDateRange] = useState<TimePeriod>('all-time');
  const [aggregation, setAggregation] = useState<AggregationBucket | null>(null);
  const [initialDateRangeSet, setInitialDateRangeSet] = useState(false);

  const handleRetryMigration = async () => {
    if (!biomarker) return;
    try {
      const updated = await biomarkerService.retryMigration(biomarker.id);
      setBiomarker(updated);
    } catch (error) {
      console.error("Failed to retry migration", error);
    }
  };

  // Polling for migration status (kept on the detail page so the trend chart
  // refreshes the moment a telemetry↔FHIR migration completes).
  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    let staleCount = 0;
    let lastProgress = biomarker?.meta_data?.migration_progress ?? 0;

    if (biomarker?.meta_data?.migration_status === 'in_progress') {
      interval = setInterval(async () => {
        try {
          const updated = await biomarkerService.getBiomarkerById(biomarker.id);

          const currentProgress = updated.meta_data?.migration_progress ?? 0;
          if (currentProgress === lastProgress) {
             staleCount++;
          } else {
             staleCount = 0;
             lastProgress = currentProgress;
          }

          if (staleCount >= 10 && updated.meta_data?.migration_status === 'in_progress') {
             updated.meta_data.migration_status = 'failed';
             updated.meta_data.migration_error = 'Migration stalled. The background worker may be offline or unresponsive.';
          }

          setBiomarker(updated);

          if (updated.meta_data?.migration_status !== 'in_progress') {
            clearInterval(interval);
            if (currentProgress === 100 && currentPatient?.id) {
              const trendsData = await getBiomarkerTrends('', updated.slug, dateRange, currentPatient.id, aggregation || undefined);
              if (trendsData.biomarkers && trendsData.biomarkers[updated.slug]) {
                setTrends(trendsData.biomarkers[updated.slug]);
              }
            }
          }
        } catch (error) {
          console.error("Failed to poll biomarker migration status", error);
        }
      }, 3000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [biomarker?.id, biomarker?.meta_data?.migration_status, biomarker?.meta_data?.migration_progress, currentPatient?.id, dateRange, aggregation]);

  const breadcrumbs = React.useMemo(() => [
    { label: t('biomarker_catalog.title'), path: '/catalogs?type=biomarker' }
  ], [t]);

  const subtitle = React.useMemo(() => (
    <div className="flex flex-col space-y-2">
      <div className="flex items-center space-x-2">
        <span className="px-3 py-1 bg-gray-100 dark:bg-dark-bg rounded-full text-[10px] font-black uppercase tracking-widest text-gray-500 dark:text-dark-muted border border-gray-200 dark:border-dark-border w-fit">
          {biomarker?.category || 'General'}
        </span>
        {biomarker?.is_telemetry ? (
          <span className="flex items-center space-x-1 px-2.5 py-1 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded-full text-[10px] font-black uppercase tracking-widest border border-indigo-100 dark:border-indigo-900/30 w-fit" title="High-frequency telemetry data from IoT devices">
            <Activity className="w-3 h-3" />
            <span>Telemetry</span>
          </span>
        ) : (
          <span className="flex items-center space-x-1 px-2.5 py-1 bg-slate-50 dark:bg-slate-900/20 text-slate-500 dark:text-slate-400 rounded-full text-[10px] font-black uppercase tracking-widest border border-slate-200 dark:border-slate-800 w-fit" title="Standard clinical FHIR data">
            <Box className="w-3 h-3" />
            <span>FHIR</span>
          </span>
        )}
      </div>
    </div>
  ), [biomarker?.category, biomarker?.is_telemetry]);

  const headerIcon = React.useMemo(() => <Activity className="w-8 h-8" />, []);

  const interpretation = React.useMemo(() => {
    if (!trends || trends.length === 0 || !biomarker) return 'Normal';
    const latest = trends[trends.length - 1];
    const mockObs: any = {
      value: { raw: latest.value },
      interpretation: latest.status || 'Normal',
      referenceRange: {
        min: biomarker.reference_range_min,
        max: biomarker.reference_range_max,
        displayText: biomarker.reference_range_min != null || biomarker.reference_range_max != null
          ? `${biomarker.reference_range_min ?? '0'} - ${biomarker.reference_range_max ?? '∞'}`
          : '--'
      }
    };
    return getFinalStatus(mockObs);
  }, [trends, biomarker]);

  const filteredTrends = React.useMemo(() => {
    if (!trends || trends.length === 0) return [];
    if (dateRange === 'all-time') return trends;

    const cutoff = getCutoffDate(dateRange as TimePeriod);
    return trends.filter((d: any) => new Date(d.date) >= cutoff);
  }, [trends, dateRange]);

  useEffect(() => {
    if (decodedId) {
      setCurrentBiomarkerId(decodedId);
    }
    return () => setCurrentBiomarkerId(null);
  }, [decodedId, setCurrentBiomarkerId]);

  useEffect(() => {
    if (biomarker?.is_telemetry && dateRange) {
      setAggregation(DEFAULT_AGGREGATIONS[dateRange as TimePeriod] || '1 day');
    } else {
      setAggregation(null);
    }
  }, [dateRange, biomarker?.is_telemetry]);

  useEffect(() => {
    const fetchData = async () => {
      if (!decodedId) return;
      if (!biomarker) setLoading(true);
      try {
        let bioData = await biomarkerService.getBiomarkerById(decodedId).catch(() => {
             console.warn("Biomarker definition not found for ID:", decodedId);
             return null;
        });

        if (!bioData) {
          const trendsData = currentPatient?.id ? await getBiomarkerTrends('', decodedId, dateRange, currentPatient.id, aggregation || undefined) : { biomarkers: {} };
          if (trendsData.biomarkers && trendsData.biomarkers[decodedId]) {
            const biomarkerTrends = trendsData.biomarkers[decodedId];
            if (biomarkerTrends.length > 0) {
              const latest = biomarkerTrends[biomarkerTrends.length - 1];
              bioData = {
                id: decodedId,
                slug: decodedId,
                name: latest.name || decodedId,
                category: 'Uncategorized',
                info: '',
                reference_range_min: null,
                reference_range_max: null,
                is_telemetry: latest.source_type === 'telemetry'
              } as any;

              if (bioData?.is_telemetry && !initialDateRangeSet) {
                setDateRange('last-24-hours');
                setInitialDateRangeSet(true);
                return;
              }
              setInitialDateRangeSet(true);

              setBiomarker(bioData);
              setTrends(biomarkerTrends);
            } else {
              setBiomarker(null);
              setTrends([]);
            }
          } else {
            setBiomarker(null);
            setTrends([]);
          }
        } else {
          if (bioData?.is_telemetry && !initialDateRangeSet) {
            setDateRange('last-24-hours');
            setInitialDateRangeSet(true);
            return;
          }
          setInitialDateRangeSet(true);

          setBiomarker(bioData);
          const trendsData = currentPatient?.id ? await getBiomarkerTrends('', bioData.slug, dateRange, currentPatient.id, aggregation || undefined) : { biomarkers: {} };
          if (trendsData.biomarkers && trendsData.biomarkers[bioData.slug]) {
            setTrends(trendsData.biomarkers[bioData.slug]);
          } else {
            setTrends([]);
          }
        }
      } catch (error) {
        console.error("Failed to fetch biomarker details", error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    // 'biomarker' + 'initialDateRangeSet' are intentionally excluded — they're
    // set inside this effect and would cause a fetch loop if listed as deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decodedId, currentPatient?.id, dateRange, aggregation]);

  if (!currentPatient) {
    return <NoPatientState icon={Activity} contextKey="biomarker_detail" />;
  }

  if (loading) {
    return <LoadingState variant="section" showText={false} />;
  }

  if (!biomarker) {
    return (
      <div className="max-w-3xl mx-auto py-20 text-center">
        <div className="w-20 h-20 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-6">
          <Activity className="w-10 h-10 text-gray-300" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 dark:text-dark-text">Biomarker Not Found</h2>
        <p className="text-gray-500 mt-2">The metric you are looking for does not exist in our clinical catalog.</p>
        <button
          onClick={() => navigate('/biomarkers')}
          className="mt-6 px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
        >
          Back to Metrics
        </button>
      </div>
    );
  }

  const TABS: { id: BiomarkerTabId; label: string; icon: React.ComponentType<any> }[] = [
    { id: 'trend', label: t('biomarkers.tab_trend', 'Trend'), icon: TrendingUp },
    { id: 'info', label: t('biomarkers.tab_clinical', 'Clinical'), icon: Info },
    { id: 'history', label: t('biomarkers.tab_history', 'History'), icon: Calendar },
    { id: 'insights', label: t('biomarkers.tab_insights', 'Insights'), icon: Activity },
    { id: 'relations', label: t('biomarkers.tab_relations', 'Relations'), icon: Share2 },
    { id: 'technical', label: t('biomarkers.tab_technical', 'Technical'), icon: Tag },
  ];

  // Full title + explanatory description for the active tab's (i) popover.
  const TAB_INFO: Record<BiomarkerTabId, { title: string; description: string }> = {
    trend: { title: t('biomarkers.longitudinal_trend'), description: t('biomarkers.tab_trend_desc') },
    info: { title: t('biomarkers.clinical_significance'), description: t('biomarkers.tab_clinical_desc') },
    history: { title: t('biomarkers.observations'), description: t('biomarkers.tab_history_desc') },
    insights: { title: t('biomarkers.ai_insights'), description: t('biomarkers.tab_insights_desc') },
    relations: { title: t('biomarkers.tab_relations', 'Relations'), description: t('biomarkers.tab_relations_desc') },
    technical: { title: t('biomarkers.technical_metadata'), description: t('biomarkers.tab_technical_desc') },
  };
  const activeTabInfo = TAB_INFO[activeTab] ?? { title: '', description: '' };

  return (
    <div className="max-w-6xl mx-auto pb-20">
      <PageHeader
        title={biomarker.name}
        subtitle={subtitle}
        icon={headerIcon}
        breadcrumbs={breadcrumbs}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center gap-3">
            <a
              href={`/catalogs?type=biomarker&item=${biomarker.id}`}
              className="flex items-center space-x-2 px-6 py-2.5 rounded-xl font-bold text-sm transition-all active:scale-95 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg"
              title={t('biomarkers.manage_in_catalog', 'Manage in Catalog')}
            >
              <Database className="w-4 h-4" />
              <span>{t('biomarkers.manage_in_catalog', 'Manage in Catalog')}</span>
            </a>
            <button className="p-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-gray-400 hover:text-blue-600 transition-all shadow-sm">
              <Share2 className="w-5 h-5" />
            </button>
            <button className="p-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-gray-400 hover:text-blue-600 transition-all shadow-sm">
              <Printer className="w-5 h-5" />
            </button>
          </div>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        {/* Left Column: Primary Visualization & Details */}
        <div className="xl:col-span-2 space-y-6">

          {/* KPI Summary Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
              <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('biomarkers.latest_result')}</p>
              <div className="flex items-baseline space-x-1">
                <span className="text-xl font-black text-gray-900 dark:text-dark-text">{trends.length > 0 ? formatBiomarkerValue(trends[trends.length - 1].value, precisionProfile) : '--'}</span>
                <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase">{trends.length > 0 ? formatUnit(trends[trends.length - 1].unit) : biomarker.preferred_unit_symbol ? formatUnit(biomarker.preferred_unit_symbol) : ''}</span>
              </div>
            </div>
            <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
              <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('biomarkers.clinical_reference')}</p>
              <div className="flex items-baseline">
                <span className="text-sm font-bold text-blue-600 dark:text-blue-400 font-mono leading-none">
                  {biomarker.reference_range_min != null || biomarker.reference_range_max != null
                    ? `${biomarker.reference_range_min ?? '0'} - ${biomarker.reference_range_max ?? '∞'}`
                    : 'undefined'}
                </span>
              </div>
            </div>
            <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
              <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('biomarkers.avg_overall')}</p>
              <div className="flex items-baseline">
                <span className="text-xl font-black text-gray-700 dark:text-dark-text">{trends.length > 0 ? (trends.reduce((a, b) => a + b.value, 0) / trends.length).toFixed(1) : '--'}</span>
              </div>
            </div>
            <div className="bg-white dark:bg-dark-surface p-4 rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm">
              <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('biomarkers.total_records')}</p>
              <div className="flex items-baseline">
                <span className="text-xl font-black text-gray-700 dark:text-dark-text">{trends.length}</span>
                <span className="ml-1 text-[10px] font-bold text-gray-400 uppercase">{t('biomarkers.tests')}</span>
              </div>
            </div>
          </div>

          {/* Details Section (Tabs) */}
          <div ref={tabsRef} className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border shadow-sm min-h-[550px] flex flex-col scroll-mt-32">
            <div className="px-8 pt-8 pb-4 border-b border-gray-50 dark:border-dark-border">
              <div className="flex items-center gap-2 flex-wrap">
                <div className="flex flex-wrap items-center gap-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl w-fit max-w-full">
                  {TABS.map(tabItem => {
                    const Icon = tabItem.icon;
                    return (
                      <button
                        key={tabItem.id}
                        onClick={() => handleTabChange(tabItem.id)}
                        className={`flex items-center space-x-1.5 px-4 py-2 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === tabItem.id ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                      >
                        <Icon className="w-3.5 h-3.5" />
                        <span>{tabItem.label}</span>
                      </button>
                    );
                  })}
                </div>
                <TabInfoButton
                  title={activeTabInfo.title}
                  description={activeTabInfo.description}
                  className="ml-auto shrink-0"
                />
              </div>
            </div>

            <div className="flex-1 overflow-hidden">
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
              {activeTab === 'insights' && <BiomarkerInsightsTab biomarker={biomarker} />}
              {activeTab === 'relations' && <BiomarkerRelationsTab biomarker={biomarker} />}
              {activeTab === 'technical' && (
                <BiomarkerTechnicalTab
                  biomarker={biomarker}
                  fallbackUnit={trends.length > 0 ? trends[0].unit : undefined}
                />
              )}
            </div>
          </div>
        </div>

        {/* Right Column: Sidebar Stats */}
        <div className="space-y-6">
          <MigrationProgressIndicator
            status={biomarker.meta_data?.migration_status as any}
            progress={biomarker.meta_data?.migration_progress}
            errorMessage={biomarker.meta_data?.migration_error}
            onRetry={handleRetryMigration}
          />
          <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center justify-between mb-8">
              <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">{t('biomarkers.patient_snapshot')}</h4>
              {trends.length > 0 && (
                <span className={`px-2.5 py-1 rounded-lg text-[10px] font-black uppercase tracking-wider border ${getStatusColorClass(interpretation)}`}>
                  {interpretation}
                </span>
              )}
            </div>

            <div className="space-y-8">
              <div>
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2">{t('biomarkers.latest_result')}</p>
                <div className="flex items-baseline space-x-2">
                  <span className="text-4xl font-black text-gray-900 dark:text-dark-text tracking-tighter">{trends.length > 0 ? formatBiomarkerValue(trends[trends.length - 1].value, precisionProfile) : '--'}</span>
                  <span className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase">{trends.length > 0 ? formatUnit(trends[trends.length - 1].unit) : biomarker.preferred_unit_symbol ? formatUnit(biomarker.preferred_unit_symbol) : ''}</span>
                </div>
              </div>

              <div className="p-5 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl border border-gray-100 dark:border-dark-border shadow-inner">
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2">{t('biomarkers.clinical_reference')}</p>
                <div className="flex items-baseline space-x-2">
                  <span className={`${biomarker.reference_range_min != null || biomarker.reference_range_max != null ? 'text-xl font-black text-blue-600 dark:text-blue-400 font-mono tracking-tighter' : 'text-sm font-medium text-gray-300 dark:text-dark-muted/30 italic'}`}>
                    {biomarker.reference_range_min != null && biomarker.reference_range_max != null
                      ? `${biomarker.reference_range_min} - ${biomarker.reference_range_max}`
                      : biomarker.reference_range_min != null
                        ? `> ${biomarker.reference_range_min}`
                        : biomarker.reference_range_max != null
                          ? `< ${biomarker.reference_range_max}`
                          : 'undefined'
                    }
                  </span>
                  {(biomarker.reference_range_min != null || biomarker.reference_range_max != null) && (
                    <span className="text-xs font-bold text-gray-400 dark:text-dark-muted">{trends.length > 0 ? formatUnit(trends[0].unit) : ''}</span>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-6 pt-4 border-t border-gray-50 dark:border-dark-border">
                <div>
                  <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('biomarkers.avg_6mo')}</p>
                  <p className="text-lg font-black text-gray-700 dark:text-dark-text leading-none">
                    {trends.length > 0 ? (trends.reduce((a, b) => a + b.value, 0) / trends.length).toFixed(1) : '--'}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('biomarkers.tests')}</p>
                  <p className="text-lg font-black text-gray-700 dark:text-dark-text leading-none">{trends.length} <span className="text-[9px] font-bold text-gray-400 uppercase ml-0.5">Rec.</span></p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

    </div>
  );
};

export default BiomarkerDetail;
