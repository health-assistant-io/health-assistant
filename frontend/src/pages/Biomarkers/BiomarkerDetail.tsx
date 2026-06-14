import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, ChevronRight, Activity, Info, Calendar, TrendingUp, Tag, Layers, Share2, Printer, Trash2, Search, Filter, ZoomIn, RefreshCw, Grid, Box, Edit2, Check, X, Lock, Unlock, Save } from 'lucide-react';
import { LoadingState } from '../../components/ui/LoadingState';
import { formatUnit, getFinalStatus, getStatusColorClass, isAbnormal } from '../../utils/biomarkerUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { InfoTooltip } from '../../components/ui/InfoTooltip';
import biomarkerService from '../../services/biomarkerService';
import { getBiomarkerTrends } from '../../services/analyticsService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { useTabScroll } from '../../hooks/useTabScroll';
import { Biomarker, Unit } from '../../types/biomarker';
import { TimePeriod, TIME_RANGES, AGGREGATION_OPTIONS, DEFAULT_AGGREGATIONS, AggregationBucket, getCutoffDate } from '../../config/timeRanges';
import { UnitSelector } from '../../components/ui/UnitSelector';
import { BiomarkerConfigPanel } from '../../components/biomarkers/BiomarkerConfigPanel';
import { MigrationProgressIndicator } from '../../components/biomarkers/MigrationProgressIndicator';
import LineChart from '../../components/charts/LineChart';
import { RichTextEditor } from '../../components/ui/RichTextEditor';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const BiomarkerDetail: React.FC = () => {
  // TODO: Expand biomarker detail UI with a tabbed interface (Scope, High/Low Implications, Related Symptoms) (from DEVELOPMENT_PLAN.md)
  const { t } = useTranslation();
  const { biomarkerId } = useParams<{ biomarkerId: string }>();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const { showReferenceRanges, setShowReferenceRanges } = useSettingsStore();
  
  const decodedId = decodeURIComponent(biomarkerId || '');

  // Use specific selectors to avoid re-renders when other UI state (like pageHeaderConfig) changes
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const setCurrentBiomarkerId = useUIStore(state => state.setCurrentBiomarkerId);

  const [biomarker, setBiomarker] = useState<Biomarker | null>(null);
  const [trends, setTrends] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'info' | 'history' | 'insights'>('info');
  const tabsRef = React.useRef<HTMLDivElement>(null);
  
  // Auto-scroll when tab changes
  useTabScroll(tabsRef, activeTab);
  const [isDeleting, setIsDeleting] = useState(false);
  const [dateRange, setDateRange] = useState<TimePeriod>('all-time');
  const [aggregation, setAggregation] = useState<AggregationBucket | null>(null);
  const [initialDateRangeSet, setInitialDateRangeSet] = useState(false);
  const [chartType, setChartType] = useState<'line' | 'area' | 'bar'>('line');
  const [showGrid, setShowGrid] = useState(true);
  const [showSpikes, setShowSpikes] = useState(true);
  const [showZoom, setShowZoom] = useState(true);

  // Global editing toggle
  const [isGlobalEditing, setIsGlobalEditing] = useState(false);
  
  const handleRetryMigration = async () => {
    if (!biomarker) return;
    try {
      const updated = await biomarkerService.retryMigration(biomarker.id);
      setBiomarker(updated);
    } catch (error) {
      console.error("Failed to retry migration", error);
    }
  };

  // Polling for migration status
  useEffect(() => {
    let interval: NodeJS.Timeout;
    let staleCount = 0;
    let lastProgress = biomarker?.meta_data?.migration_progress ?? 0;
    
    if (biomarker?.meta_data?.migration_status === 'in_progress') {
      interval = setInterval(async () => {
        try {
          const updated = await biomarkerService.getBiomarkerById(biomarker.id);
          
          // Check if progress is stalled (e.g. celery worker died)
          const currentProgress = updated.meta_data?.migration_progress ?? 0;
          if (currentProgress === lastProgress) {
             staleCount++;
          } else {
             staleCount = 0;
             lastProgress = currentProgress;
          }

          // If no progress for 30 seconds (10 ticks of 3s), assume stuck
          if (staleCount >= 10 && updated.meta_data?.migration_status === 'in_progress') {
             // Mock a failure state in the UI so the user gets the retry button
             updated.meta_data.migration_status = 'failed';
             updated.meta_data.migration_error = 'Migration stalled. The background worker may be offline or unresponsive.';
          }

          setBiomarker(updated);
          
          if (updated.meta_data?.migration_status !== 'in_progress') {
            clearInterval(interval);
            // Refresh trends once migration is complete
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
  
  // Memoize breadcrumbs and subtitle to prevent PageHeader re-renders
  const breadcrumbs = React.useMemo(() => [
    { label: t('biomarker_catalog.title'), path: '/biomarkers/catalog' }
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
      <p className="text-gray-500 dark:text-dark-muted font-mono text-xs uppercase tracking-tighter">
        {biomarker?.coding_system === 'loinc' ? 'LOINC CODE' : 'CUSTOM CODE'}: {biomarker?.code || biomarker?.slug}
      </p>
    </div>
  ), [biomarker?.category, biomarker?.code, biomarker?.coding_system, biomarker?.slug, biomarker?.is_telemetry]);

  const headerIcon = React.useMemo(() => <Activity className="w-8 h-8" />, []);
  
  // Info editing state
  const [isEditingInfo, setIsEditingInfo] = useState(false);
  const [infoContent, setInfoContent] = useState('');
  const [rangeMin, setRangeMin] = useState<string>('');
  const [rangeMax, setRangeMax] = useState<string>('');
  const [preferredUnitId, setPreferredUnitId] = useState<string>('');
  const [isTelemetry, setIsTelemetry] = useState<boolean>(false);
  const [units, setUnits] = useState<Unit[]>([]);
  const [isSaving, setIsSaving] = useState(false);

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

  const handleDelete = async () => {
    if (!biomarker) return;
    
    showConfirmation({
      title: 'Delete Biomarker Definition',
      message: `Are you sure you want to delete "${biomarker.name}"? This will not delete patient data, but it will disconnect existing results from this definition.`,
      confirmLabel: 'Delete Definition',
      confirmVariant: 'danger',
      onConfirm: async () => {
        setIsDeleting(true);
        try {
          await biomarkerService.deleteBiomarker(biomarker.id);
          navigate('/biomarkers/catalog');
        } catch (error) {
          console.error("Failed to delete biomarker", error);
        } finally {
          setIsDeleting(false);
        }
      }
    });
  };

  const handleSaveInfo = async () => {
    if (!biomarker) return;
    setIsSaving(true);
    try {
      const updated = await biomarkerService.updateBiomarker(biomarker.id, { 
        info: infoContent
      });
      setBiomarker(updated);
      setIsEditingInfo(false);
    } catch (error) {
      console.error("Failed to save info", error);
    } finally {
      setIsSaving(false);
    }
  };

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
        let bioData = await biomarkerService.getBiomarkerById(decodedId).catch(err => {
             console.warn("Biomarker definition not found for ID:", decodedId);
             return null;
        });
        
        try {
            const unitsData = await biomarkerService.getUnits();
            setUnits(unitsData);
        } catch (e) {
            console.error("Failed to load units", e);
        }

        if (!bioData) {
          const trendsData = currentPatient?.id ? await getBiomarkerTrends('', decodedId, dateRange, currentPatient.id, aggregation || undefined) : { biomarkers: {} };
          if (trendsData.biomarkers && trendsData.biomarkers[decodedId]) {
            const biomarkerTrends = trendsData.biomarkers[decodedId];
            if (biomarkerTrends.length > 0) {
              const latest = biomarkerTrends[biomarkerTrends.length - 1];
              // Create a mock biomarker definition from the trend data
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
                return; // Will re-trigger effect
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
            return; // Will re-trigger effect
          }
          setInitialDateRangeSet(true);

          setBiomarker(bioData);
          setInfoContent(bioData.info || '');
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
  }, [decodedId, currentPatient?.id, dateRange, aggregation]);

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
               onClick={() => setIsGlobalEditing(!isGlobalEditing)} 
               className={`flex items-center space-x-2 px-6 py-2.5 rounded-xl font-bold text-sm transition-all active:scale-95 border ${
                 isGlobalEditing 
                   ? 'bg-orange-600 text-white border-orange-700 shadow-lg shadow-orange-900/20' 
                   : 'bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg'
               }`}
            >
               {isGlobalEditing ? <Unlock className="w-4 h-4" /> : <Lock className="w-4 h-4" />}
               <span>{isGlobalEditing ? t('biomarker_catalog.finish_editing') : t('biomarker_catalog.edit_catalog')}</span>
            </button>
            <button 
              onClick={handleDelete}
              disabled={isDeleting}
              className="p-2.5 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-colors border border-transparent hover:border-red-100 dark:hover:border-red-900/40 active:scale-95 disabled:opacity-50"
              title={t('biomarker_catalog.delete_biomarker')}
            >
              <Trash2 className="w-5 h-5" />
            </button>
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
                <span className="text-xl font-black text-gray-900 dark:text-dark-text">{trends.length > 0 ? trends[trends.length - 1].value : '--'}</span>
                <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase">{trends.length > 0 ? formatUnit(trends[trends.length - 1].unit) : ''}</span>
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

          {/* Primary Trend Chart Section */}
          <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border shadow-sm flex flex-col">
            <div className="p-6 sm:p-8 border-b border-gray-50 dark:border-dark-border flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                  <TrendingUp className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight">{t('biomarkers.longitudinal_trend')}</h3>
                  <p className="text-xs text-gray-400 font-medium">{t('biomarkers.historical_trajectory')}</p>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center space-x-2">
                  <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
                    {TIME_RANGES.map(range => (
                      <button 
                        key={range.id}
                        onClick={() => setDateRange(range.id)}
                        className={`px-3 py-1.5 text-[10px] font-black rounded-lg transition-all ${dateRange === range.id ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                      >
                        {range.shortLabel}
                      </button>
                    ))}
                  </div>
                  
                  {biomarker?.is_telemetry && aggregation && (
                    <div className="flex items-center ml-2 bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
                      <select
                        value={aggregation}
                        onChange={(e) => setAggregation(e.target.value as AggregationBucket)}
                        className="bg-transparent text-[10px] font-black text-gray-700 dark:text-gray-300 outline-none cursor-pointer pl-2 pr-6 py-1.5 appearance-none"
                        style={{ backgroundImage: 'url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%239CA3AF%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right .5rem top 50%', backgroundSize: '.65rem auto' }}
                      >
                        {AGGREGATION_OPTIONS.map(opt => (
                          <option key={opt.id} value={opt.id}>{opt.label} avg</option>
                        ))}
                      </select>
                    </div>
                  )}

                  <InfoTooltip 
                    className="p-1"
                    content={biomarker?.is_telemetry 
                      ? "High-frequency telemetry data is automatically aggregated into dynamic time buckets (e.g. 15-minute, 1-hour, or 1-day averages) based on the selected time range to ensure fast loading." 
                      : "Standard clinical FHIR data displays every recorded data point exactly as it was measured without aggregation."}
                  />
                </div>

                <div className="flex items-center space-x-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
                  {[
                    { id: 'line', icon: TrendingUp },
                    { id: 'area', icon: Layers },
                    { id: 'bar', icon: Grid }
                  ].map(type => (
                    <button 
                      key={type.id}
                      onClick={() => setChartType(type.id as any)}
                      className={`p-1.5 rounded-lg transition-all ${chartType === type.id ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                      title={type.id.charAt(0).toUpperCase() + type.id.slice(1)}
                    >
                      <type.icon className="w-3.5 h-3.5" />
                    </button>
                  ))}
                </div>

                <button 
                  onClick={() => setShowGrid(!showGrid)}
                  className={`p-2 rounded-xl transition-all border ${showGrid ? 'bg-blue-50 dark:bg-blue-900/20 border-blue-100 text-blue-600' : 'bg-white dark:bg-dark-surface border-gray-100 text-gray-400'}`}
                  title="Toggle Grid"
                >
                  <Layers className="w-4 h-4" />
                </button>

                {(biomarker.reference_range_min != null || biomarker.reference_range_max != null) && (
                  <button 
                    onClick={() => setShowReferenceRanges(!showReferenceRanges)}
                    className={`p-2 rounded-xl transition-all border ${showReferenceRanges ? 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-100 text-emerald-600' : 'bg-white dark:bg-dark-surface border-gray-100 text-gray-400'}`}
                    title="Toggle Reference Range Area"
                  >
                    <Box className="w-4 h-4" />
                  </button>
                )}

                {biomarker.is_telemetry && (
                  <button 
                    onClick={() => setShowSpikes(!showSpikes)}
                    className={`p-2 rounded-xl transition-all border ${showSpikes ? 'bg-rose-50 dark:bg-rose-900/20 border-rose-100 text-rose-600' : 'bg-white dark:bg-dark-surface border-gray-100 text-gray-400'}`}
                    title="Toggle Min/Max Spikes"
                  >
                    <Activity className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>

            <div className="p-4 sm:p-8 h-[400px]">
              {filteredTrends.length > 0 ? (
                <LineChart 
                  data={filteredTrends.map(t => ({ 
                    name: new Date(t.date).toLocaleDateString(), 
                    tooltipLabel: new Date(t.date).toLocaleString(undefined, {
                      year: 'numeric',
                      month: 'short',
                      day: 'numeric',
                      hour: biomarker?.is_telemetry ? 'numeric' : undefined,
                      minute: biomarker?.is_telemetry ? '2-digit' : undefined,
                    }),
                    value: t.value,
                    min_value: t.min_value,
                    max_value: t.max_value,
                    range: (t.min_value !== undefined && t.max_value !== undefined) ? [t.min_value, t.max_value] : undefined
                  }))} 
                  dataKey="value" 
                  xAxisKey="name"
                  color="#3b82f6"
                  referenceRange={{
                    min: biomarker.reference_range_min,
                    max: biomarker.reference_range_max
                  }}
                  showReferenceLines={showReferenceRanges}
                  chartType={chartType}
                  showGrid={showGrid}
                  showSpikes={showSpikes && biomarker.is_telemetry}
                  showBrush={true}
                  interactiveZoom={showZoom}
                  height="100%"
                />
              ) : (
                <div className="h-full flex flex-col items-center justify-center text-center opacity-40">
                  <Calendar className="w-12 h-12 mb-4 text-gray-300" />
                  <p className="font-bold text-gray-500 uppercase tracking-widest text-sm">No historical data available</p>
                </div>
              )}
            </div>
          </div>

          {/* Secondary Details Section (Tabs) */}
          <div ref={tabsRef} className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border shadow-sm min-h-[400px] flex flex-col scroll-mt-32">
            <div className="px-8 pt-8 pb-4 border-b border-gray-50 dark:border-dark-border">
              <div className="flex items-center space-x-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl w-fit">
                <button 
                  onClick={() => setActiveTab('info')}
                  className={`px-6 py-2 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'info' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                >
                  {t('biomarkers.clinical_significance')}
                </button>
                <button 
                  onClick={() => setActiveTab('history')}
                  className={`px-6 py-2 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'history' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                >
                  {t('biomarkers.observations') || 'Observations'}
                </button>
                <button 
                  onClick={() => setActiveTab('insights')}
                  className={`px-6 py-2 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'insights' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                >
                  {t('biomarkers.ai_insights')}
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-hidden">
              {activeTab === 'info' && (
                <div className="p-8 animate-in fade-in duration-300">
                  <div className="flex items-center justify-between mb-6">
                    <h3 className="flex items-center space-x-2 text-lg font-bold text-gray-900 dark:text-dark-text">
                      <Info className="w-5 h-5 text-blue-500" />
                      <span>{t('biomarkers.clinical_significance')}</span>
                    </h3>
                  </div>

                  {isEditingInfo ? (
                    <div className="space-y-4">
                      <RichTextEditor value={infoContent} onChange={setInfoContent} placeholder={t('biomarkers.historical_trajectory') || 'Add detailed clinical context here...'} />
                      <div className="flex justify-end space-x-3 pt-2">
                         <button onClick={() => setIsEditingInfo(false)} className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 transition-colors">{t('common.cancel')}</button>
                         <button onClick={handleSaveInfo} disabled={isSaving} className="flex items-center space-x-2 px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95 text-xs uppercase tracking-widest disabled:opacity-50">
                            {isSaving ? <Activity className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                            <span>{t('biomarker_catalog.save_info')}</span>
                         </button>
                      </div>
                    </div>
                  ) : (
                    <div className="group relative">
                      {isGlobalEditing && (
                        <button 
                          onClick={() => {
                            setInfoContent(biomarker.info || '');
                            setIsEditingInfo(true);
                          }} 
                          className="absolute -top-12 right-0 p-2 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-xl opacity-0 group-hover:opacity-100 transition-all border border-blue-100 dark:border-blue-800/30 shadow-sm z-10"
                          title={t('common.edit')}
                        >
                          <Edit2 className="w-4 h-4" />
                        </button>
                      )}
                      {biomarker.info ? (
                        <div className="prose dark:prose-invert max-w-none text-gray-700 dark:text-dark-text leading-relaxed">
                          {biomarker.info.includes('</') || biomarker.info.includes('<br') ? (
                            <div 
                              className="font-medium"
                              dangerouslySetInnerHTML={{ __html: biomarker.info }}
                            />
                          ) : (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{biomarker.info}</ReactMarkdown>
                          )}
                        </div>
                      ) : (
                        <div className="flex flex-col items-center justify-center py-12 text-center opacity-60 border-2 border-dashed border-gray-100 dark:border-dark-border rounded-3xl">
                          <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mb-4 text-gray-300">
                            <Layers className="w-8 h-8" />
                          </div>
                          <p className="text-gray-400 font-bold uppercase tracking-widest text-xs">{t('biomarkers.no_clinical_info')}</p>
                          {isGlobalEditing ? (
                             <button onClick={() => {
                                setInfoContent(biomarker.info || '');
                                setIsEditingInfo(true);
                             }} className="mt-4 text-blue-600 font-bold hover:underline text-sm uppercase tracking-tighter">{t('biomarkers.add_clinical_info')}</button>
                          ) : (
                             <Link to="/biomarkers/catalog" className="mt-4 text-blue-600 font-bold hover:underline text-sm uppercase tracking-tighter">{t('biomarkers.add_to_catalog')}</Link>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'history' && (
                <div className="animate-in fade-in duration-300 h-full flex flex-col">
                  <div className="flex-1 overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
                      <thead className="bg-gray-50/50 dark:bg-dark-bg/50">
                        <tr>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('dashboard.config.date_range')}</th>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.latest_result')}</th>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.standard_unit')}</th>
                          <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('common.source') || 'Source'}</th>
                          <th className="px-8 py-4 text-right text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('common.actions') || 'Actions'}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                        {filteredTrends.map((t, i) => (
                          <tr key={i} className="group hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors">
                            <td className="px-8 py-5 whitespace-nowrap text-sm font-bold text-gray-900 dark:text-dark-text">
                              {new Date(t.date).toLocaleString(undefined, { 
                                year: 'numeric', 
                                month: 'short', 
                                day: 'numeric',
                                hour: biomarker?.is_telemetry ? 'numeric' : undefined,
                                minute: biomarker?.is_telemetry ? '2-digit' : undefined
                              })}
                            </td>
                            <td className="px-8 py-5 whitespace-nowrap">
                              <span className="text-sm font-black text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-3 py-1 rounded-lg">
                                {t.value}
                              </span>
                            </td>
                            <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-500 dark:text-dark-muted font-bold">
                              {formatUnit(t.unit)}
                            </td>
                            <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-500 dark:text-dark-text font-medium">
                              <div className="flex items-center gap-2">
                                <span className={`px-2 py-0.5 rounded-full text-[10px] uppercase font-bold tracking-widest ${
                                  t.source_type === 'integration' ? 'bg-purple-50 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400' :
                                  t.source_type === 'examination' ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400' :
                                  t.source_type === 'document' ? 'bg-orange-50 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' :
                                  'bg-gray-100 text-gray-500 dark:bg-dark-bg dark:text-dark-muted'
                                }`}>
                                  {t.source_type || 'manual'}
                                </span>
                                <span>{t.source_name || t.examination_name || 'Manual Entry'}</span>
                              </div>
                            </td>
                            <td className="px-8 py-5 whitespace-nowrap text-right text-sm font-medium">
                              {t.source_type === 'integration' && (
                                <Link 
                                  to={`/settings/integrations/${t.source_id || t.source_name}`} 
                                  className="inline-flex items-center justify-center p-2 text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20 rounded-xl transition-colors"
                                  title="View Integration"
                                >
                                  <Layers className="w-4 h-4" />
                                </Link>
                              )}
                              {t.source_type === 'examination' && t.examination_id && (
                                <Link 
                                  to={`/examinations/${t.examination_id}`} 
                                  className="inline-flex items-center justify-center p-2 text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-colors"
                                  title="View Examination"
                                >
                                  <ChevronRight className="w-4 h-4" />
                                </Link>
                              )}
                            </td>
                          </tr>
                        ))}
                        {filteredTrends.length === 0 && (
                          <tr>
                            <td colSpan={5} className="px-8 py-20 text-center text-gray-400 dark:text-dark-muted font-bold uppercase tracking-widest text-xs">{t('biomarkers.no_results')}</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {activeTab === 'insights' && (
                <div className="p-8 animate-in fade-in duration-300">
                  <div className="bg-gradient-to-br from-blue-600 to-indigo-700 rounded-[2rem] p-8 text-white shadow-xl shadow-blue-200 dark:shadow-none">
                    <div className="flex items-center space-x-3 mb-6">
                      <div className="p-2 bg-white/20 rounded-xl backdrop-blur-md">
                        <Activity className="w-6 h-6 text-white" />
                      </div>
                      <h3 className="text-xl font-black uppercase tracking-tight">{t('biomarkers.smart_analysis')}</h3>
                    </div>
                    <p className="text-blue-50 leading-relaxed mb-8 font-medium">
                      Based on the longitudinal data for {biomarker.name}, the trend appears to be stable. 
                      Maintaining current lifestyle factors is recommended. Clinical context suggest that levels within this range are optimal for patients in your age demographic.
                    </p>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="bg-white/10 backdrop-blur-md p-5 rounded-2xl border border-white/10 shadow-inner">
                        <p className="text-[10px] font-black text-blue-200 uppercase tracking-widest mb-1">{t('documents_explorer.status')}</p>
                        <p className="text-lg font-black">Within Range</p>
                      </div>
                      <div className="bg-white/10 backdrop-blur-md p-5 rounded-2xl border border-white/10 shadow-inner">
                        <p className="text-[10px] font-black text-blue-200 uppercase tracking-widest mb-1">Change</p>
                        <p className="text-lg font-black">+0.4% (Steady)</p>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right Column: Sidebar Stats & Metadata */}
        <div className="space-y-6">
          <MigrationProgressIndicator 
            status={biomarker.meta_data?.migration_status as any}
            progress={biomarker.meta_data?.migration_progress}
            errorMessage={biomarker.meta_data?.migration_error}
            onRetry={handleRetryMigration}
          />
          {isGlobalEditing && (
            <BiomarkerConfigPanel 
              biomarker={biomarker}
              units={units}
              isEditable={isGlobalEditing && biomarker.meta_data?.migration_status !== 'in_progress'}
              onUnitsUpdated={setUnits}
              onSuccess={(updated) => {
                setBiomarker(updated);
                // Update local state flags so UI reflects changes instantly
                setIsTelemetry(updated.is_telemetry || false);
              }}
            />
          )}
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
                  <span className="text-4xl font-black text-gray-900 dark:text-dark-text tracking-tighter">{trends.length > 0 ? trends[trends.length - 1].value : '--'}</span>
                  <span className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase">{trends.length > 0 ? formatUnit(trends[trends.length - 1].unit) : ''}</span>
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

          <div className="bg-gray-50 dark:bg-dark-bg/30 rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border">
            <div className="flex items-center space-x-2 mb-6">
              <Tag className="w-4 h-4 text-gray-400" />
              <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.technical_metadata')}</h4>
            </div>
            <div className="space-y-6">
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-500 font-bold uppercase tracking-tighter">{t('biomarkers.standard_unit')}</span>
                <span className="text-xs font-black text-gray-700 dark:text-dark-text">{trends.length > 0 ? formatUnit(trends[0].unit) : '--'}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-500 font-bold uppercase tracking-tighter">
                  {biomarker.coding_system === 'loinc' ? 'LOINC CODE' : 'CUSTOM CODE'}
                </span>
                <span className="text-[10px] font-mono font-black bg-white dark:bg-dark-surface px-2 py-1 rounded border border-gray-200 dark:border-dark-border shadow-sm">
                  {biomarker.code || biomarker.slug}
                </span>
              </div>
              
              {biomarker.aliases && biomarker.aliases.length > 0 && (
                <div className="pt-4 border-t border-gray-100 dark:border-white/5">
                  <span className="text-xs text-gray-500 font-bold uppercase tracking-tighter block mb-3">{t('biomarkers.known_aliases')}</span>
                  <div className="flex flex-wrap gap-2">
                    {biomarker.aliases.map((alias, idx) => (
                      <div 
                        key={idx} 
                        className="px-2.5 py-1 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-lg text-[10px] font-black text-blue-600 dark:text-blue-400 uppercase tracking-tight shadow-sm hover:scale-105 transition-transform cursor-default"
                      >
                        {alias}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

    </div>
  );
};

export default BiomarkerDetail;
