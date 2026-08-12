import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, Calendar, TrendingUp, Box, Share2, Database, Tag, Info, Plus, LayoutDashboard } from 'lucide-react';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { getFinalStatus, getStatusColorClass } from '../../utils/biomarkerUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { TabInfoButton } from '../../components/ui/TabInfoButton';
import biomarkerService from '../../services/biomarkerService';
import { getBiomarkerTrends } from '../../services/analyticsService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { useTabScroll } from '../../hooks/useTabScroll';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { Biomarker } from '../../types/biomarker';
import { TimePeriod, DEFAULT_AGGREGATIONS, AggregationBucket, getCutoffDate } from '../../config/timeRanges';
import { MigrationProgressIndicator } from '../../components/biomarkers/MigrationProgressIndicator';
import { BiomarkerSnapshotCard } from '../../components/biomarkers/BiomarkerSnapshotCard';
import { LogBiomarkerReadingModal } from '../../components/biomarkers/LogBiomarkerReadingModal';
import { EditBiomarkerReadingModal } from '../../components/biomarkers/EditBiomarkerReadingModal';
import { deleteObservation, getObservation } from '../../services/observationService';
import type { Observation } from '../../types/observation';
import { useBiomarkerPrecisionProfile } from '../../hooks/useBiomarkerPrecision';
import {
  BiomarkerInfoTab,
  BiomarkerHistoryTab,
  BiomarkerInsightsTab,
  BiomarkerRelationsTab,
  BiomarkerSnapshotTab,
  BiomarkerTechnicalTab,
  BiomarkerTrendTab,
} from '../../components/biomarkers/tabs';

type BiomarkerTabId = 'snapshot' | 'trend' | 'info' | 'history' | 'insights' | 'relations' | 'technical';
const VALID_TABS: BiomarkerTabId[] = ['snapshot', 'trend', 'info', 'history', 'insights', 'relations', 'technical'];

const BiomarkerDetail: React.FC = () => {
  const { t } = useTranslation();
  const { biomarkerId, activeTab: routeTab } = useParams<{ biomarkerId: string; activeTab: string }>();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const precisionProfile = useBiomarkerPrecisionProfile();

  // The right sidebar (Patient Snapshot) only renders at xl+. Below that, the
  // snapshot becomes a tab inside the tab strip so mobile/small-width users
  // get the same summary without scrolling past a duplicate KPI strip.
  const isBelowXl = useMediaQuery('(max-width: 1279px)');

  const decodedId = decodeURIComponent(biomarkerId || '');

  const setCurrentBiomarkerId = useUIStore(state => state.setCurrentBiomarkerId);
  const showConfirmation = useUIStore(state => state.showConfirmation);

  const [biomarker, setBiomarker] = useState<Biomarker | null>(null);
  const [trends, setTrends] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [isLogReadingOpen, setIsLogReadingOpen] = useState(false);
  const [editingObservation, setEditingObservation] = useState<Observation | null>(null);
  // Bumped after a successful manual save so the trend/history tabs refresh.
  const [readingNonce, setReadingNonce] = useState(0);

  // URL-synced active tab (deep-linkable, matches ExaminationDetail convention).
  // Trend is the default — it's the primary view when opening a biomarker.
  // The 'snapshot' tab is only valid below xl (it duplicates the sidebar).
  const sanitizeTab = (tab: string | undefined): BiomarkerTabId => {
    if (tab && VALID_TABS.includes(tab as BiomarkerTabId)) {
      if (tab === 'snapshot' && !isBelowXl) return 'trend';
      return tab as BiomarkerTabId;
    }
    return 'trend';
  };
  const initialTab: BiomarkerTabId = sanitizeTab(routeTab);
  const [activeTab, setActiveTab] = useState<BiomarkerTabId>(initialTab);
  const tabsRef = React.useRef<HTMLDivElement>(null);
  useTabScroll(tabsRef, activeTab);

  // Keep local tab in sync if the URL changes (back/forward navigation).
  useEffect(() => {
    setActiveTab(sanitizeTab(routeTab));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeTab]);

  // If the viewport crosses the xl boundary while the user is on the mobile-only
  // 'snapshot' tab, fall back to 'trend' (the sidebar takes over on desktop).
  useEffect(() => {
    if (!isBelowXl && activeTab === 'snapshot') {
      handleTabChange('trend');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBelowXl]);

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

  /** Edit: fetch the full Observation (the trend row only carries a subset),
   *  then open the edit modal. */
  const handleEditRecord = async (trendRow: any) => {
    try {
      const obs = await getObservation(trendRow.observation_id);
      setEditingObservation(obs);
    } catch (err) {
      console.error('Failed to fetch observation for edit', err);
    }
  };

  /** Delete: confirm via the global modal, then DELETE + refresh. */
  const handleDeleteRecord = (trendRow: any) => {
    showConfirmation({
      title: t('biomarkers.delete_title', { defaultValue: 'Delete Biomarker Result' }),
      message: t('biomarkers.delete_message', {
        defaultValue: 'Are you sure you want to delete this biomarker result? This action cannot be undone.',
      }),
      confirmLabel: t('common.delete', { defaultValue: 'Delete' }),
      confirmVariant: 'danger',
      onConfirm: async () => {
        await deleteObservation(trendRow.observation_id);
        setReadingNonce(n => n + 1);
      },
    });
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

  const subtitle = React.useMemo(() => (
    <div className="flex flex-col space-y-2">
      <div className="flex items-center flex-wrap gap-2">
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
        {trends.length > 0 && (
          <span
            className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border w-fit ${getStatusColorClass(interpretation)}`}
            title={t('biomarkers.latest_status_tooltip', 'Latest reading interpretation')}
          >
            {interpretation}
          </span>
        )}
      </div>
    </div>
  ), [biomarker?.category, biomarker?.is_telemetry, trends.length, interpretation, t]);

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
    // 'biomarker' + 'initialDateRangeSet' + 'readingNonce' are intentionally
    // excluded where they overlap with the listed deps — 'biomarker' is set
    // inside this effect; 'readingNonce' would be redundant with the
    // explicit refresh-on-save handler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decodedId, currentPatient?.id, dateRange, aggregation, readingNonce]);

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
    // 'snapshot' is mobile-only — it duplicates the right sidebar (hidden below xl).
    ...(isBelowXl
      ? [{ id: 'snapshot' as BiomarkerTabId, label: t('biomarkers.tab_snapshot', 'Snapshot'), icon: LayoutDashboard }]
      : []),
    { id: 'trend', label: t('biomarkers.tab_trend', 'Trend'), icon: TrendingUp },
    { id: 'info', label: t('biomarkers.tab_clinical', 'Clinical'), icon: Info },
    { id: 'history', label: t('biomarkers.tab_history', 'History'), icon: Calendar },
    { id: 'insights', label: t('biomarkers.tab_insights', 'Insights'), icon: Activity },
    { id: 'relations', label: t('biomarkers.tab_relations', 'Relations'), icon: Share2 },
    { id: 'technical', label: t('biomarkers.tab_technical', 'Technical'), icon: Tag },
  ];

  // Full title + explanatory description for the active tab's (i) popover.
  const TAB_INFO: Record<BiomarkerTabId, { title: string; description: string }> = {
    snapshot: {
      title: t('biomarkers.patient_snapshot'),
      description: t(
        'biomarkers.tab_snapshot_desc',
        'At-a-glance summary: latest reading, clinical reference range, 6-month average, and total records. The same panel appears in the sidebar on wider screens.',
      ),
    },
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
            <button
              onClick={() => setIsLogReadingOpen(true)}
              disabled={biomarker.is_telemetry}
              className="flex items-center space-x-2 px-5 py-2.5 rounded-xl font-bold text-sm transition-all active:scale-95 bg-blue-600 hover:bg-blue-700 text-white shadow-lg shadow-blue-200/50 dark:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none disabled:bg-blue-400"
              title={
                biomarker.is_telemetry
                  ? t('biomarkers.log_reading.telemetry_disabled', 'Telemetry metrics can only be populated by device sync')
                  : t('biomarkers.log_reading.title', 'Log a manual reading')
              }
            >
              <Plus className="w-4 h-4" />
              <span className="hidden sm:inline">{t('biomarkers.log_reading.button', 'Log Reading')}</span>
              <span className="sm:hidden">{t('biomarkers.log_reading.button_mobile', 'Log')}</span>
            </button>
            <a
              href={`/catalogs?type=biomarker&item=${biomarker.id}`}
              className="flex items-center space-x-2 px-6 py-2.5 rounded-xl font-bold text-sm transition-all active:scale-95 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg"
              title={t('biomarkers.manage_in_catalog', 'Manage in Catalog')}
            >
              <Database className="w-4 h-4" />
              <span className="hidden md:inline">{t('biomarkers.manage_in_catalog', 'Manage in Catalog')}</span>
            </a>
          </div>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        {/* Left Column: Primary Visualization & Details */}
        <div className="xl:col-span-2 space-y-6">

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
              {activeTab === 'snapshot' && (
                <BiomarkerSnapshotTab
                  biomarker={biomarker}
                  trends={trends}
                  precisionProfile={precisionProfile}
                  interpretation={interpretation}
                  migrationStatus={biomarker.meta_data?.migration_status as any}
                  migrationProgress={biomarker.meta_data?.migration_progress}
                  migrationError={biomarker.meta_data?.migration_error}
                  onRetryMigration={handleRetryMigration}
                />
              )}
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
                  onLogReading={() => setIsLogReadingOpen(true)}
                  onEditRecord={handleEditRecord}
                  onDeleteRecord={handleDeleteRecord}
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

        {/* Right Column: Sidebar Stats — xl+ only. Below xl the snapshot lives
            in the 'Snapshot' tab and the migration banner inside it. */}
        <div className="hidden xl:block space-y-6">
          <MigrationProgressIndicator
            status={biomarker.meta_data?.migration_status as any}
            progress={biomarker.meta_data?.migration_progress}
            errorMessage={biomarker.meta_data?.migration_error}
            onRetry={handleRetryMigration}
          />
          <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <BiomarkerSnapshotCard
              biomarker={biomarker}
              trends={trends}
              precisionProfile={precisionProfile}
              interpretation={interpretation}
            />
          </div>
        </div>
      </div>

      {currentPatient && (
        <LogBiomarkerReadingModal
          isOpen={isLogReadingOpen}
          onClose={() => setIsLogReadingOpen(false)}
          patientId={currentPatient.id}
          lockedBiomarker={biomarker}
          onSuccess={() => setReadingNonce(n => n + 1)}
        />
      )}

      {biomarker && (
        <EditBiomarkerReadingModal
          isOpen={!!editingObservation}
          onClose={() => setEditingObservation(null)}
          observation={editingObservation}
          biomarker={biomarker}
          onSuccess={() => setReadingNonce(n => n + 1)}
        />
      )}

    </div>
  );
};

export default BiomarkerDetail;
