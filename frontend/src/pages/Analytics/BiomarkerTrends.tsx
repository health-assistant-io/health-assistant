import { useEffect, useState, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { getBiomarkerTrends, getCachedBiomarkerTrends } from '../../services/analyticsService';
import LineChart from '../../components/charts/LineChart';
import { Download, TrendingUp, Search, AlertCircle, Grid, List, Table as TableIcon, ChevronDown, ListTree, Box, Layers, Calendar, X, Check, Filter, Info } from 'lucide-react';
import { useBiomarkers, Perspective } from '../../hooks/useBiomarkers';
import { TimePeriod, TIME_RANGES, DEFAULT_AGGREGATIONS } from '../../config/timeRanges';
import { useUIStore } from '../../store/slices/uiSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { InfoTooltip } from '../../components/ui/InfoTooltip';
import { BiomarkerList } from '../../components/biomarkers/BiomarkerList';
import { VisualizationSettings } from '../../components/biomarkers/VisualizationSettings';
import { isAbnormal } from '../../utils/biomarkerUtils';
import { FilterBar, useFilterState } from '../../components/ui/filters';
import { trendsBiomarkerFacets } from '../../features/biomarkers/facets';
import type { BiomarkerObservation } from '../../types/biomarker';

// --- SUB-COMPONENTS FOR PERFORMANCE ---

const CategoryDropdown = ({ activePerspective, activeTab, tabs, setActivePerspective, setActiveTab, t }: any) => {
  const [isOpen, setIsOpen] = useState(false);
  
  return (
    <div className="relative flex-shrink-0">
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center space-x-3 px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl shadow-sm text-sm font-bold text-gray-700 dark:text-dark-text active:scale-[0.98] transition-all hover:border-blue-300 dark:hover:border-blue-700/50"
      >
        <div className="p-1.5 bg-blue-50 dark:bg-blue-900/30 rounded-lg">
          {activePerspective === 'clinical' ? <Layers className="w-4 h-4 text-blue-600" /> : 
           activePerspective === 'technical' ? <Box className="w-4 h-4 text-blue-600" /> : 
           <Calendar className="w-4 h-4 text-blue-600" />}
        </div>
        <div className="text-left hidden xs:block">
          <p className="text-[9px] font-black text-gray-400 uppercase tracking-widest leading-none mb-1">
            {activePerspective === 'clinical' ? t('biomarkers.perspectives.clinical') : 
             activePerspective === 'technical' ? t('biomarkers.perspectives.technical') : 
             t('biomarkers.perspectives.examination')}
          </p>
          <p className="leading-none text-xs sm:text-sm">{activeTab}</p>
        </div>
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform duration-300 ${isOpen ? 'rotate-180' : ''}`} />
      </button>
      
      {isOpen && (
        <>
          <div className="fixed inset-0 z-[35]" onClick={() => setIsOpen(false)} />
          <div className="absolute top-full left-0 mt-2 z-[40] w-72 sm:w-80 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200 border-t-4 border-t-blue-500">
            <div className="max-h-96 overflow-y-auto p-1.5 custom-scrollbar">
              <div className="px-3 pt-3 pb-2">
                <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('biomarkers.grouping_method')}</p>
              </div>
              <div className="grid grid-cols-1 gap-1 mb-4">
                {[
                  { id: 'clinical', label: t('biomarkers.perspectives.clinical'), icon: Layers },
                  { id: 'technical', label: t('biomarkers.perspectives.technical'), icon: Box },
                  { id: 'examination', label: t('biomarkers.perspectives.examination'), icon: Calendar }
                ].map(p => (
                  <button
                    key={p.id}
                    onClick={() => { setActivePerspective(p.id as Perspective); setActiveTab('All'); setIsOpen(false); }}
                    className={`w-full text-left px-4 py-2.5 text-sm font-bold transition-all rounded-xl flex items-center justify-between ${
                      activePerspective === p.id ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600' : 'text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border'
                    }`}
                  >
                    <div className="flex items-center space-x-3">
                      <p.icon className="w-4 h-4" />
                      <span>{p.label}</span>
                    </div>
                    {activePerspective === p.id && <Check className="w-4 h-4" />}
                  </button>
                ))}
              </div>
              <div className="h-px bg-gray-100 dark:bg-dark-border mx-2 mb-2" />
              <div className="px-3 pt-2 pb-2">
                <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('biomarkers.subcategories')}</p>
              </div>
              <div className="grid grid-cols-1 gap-1">
                {tabs.map((tab: string) => (
                  <button
                    key={tab}
                    onClick={() => { setActiveTab(tab); setIsOpen(false); }}
                    className={`w-full text-left px-4 py-2.5 text-sm font-bold transition-all rounded-xl flex items-center justify-between group ${
                      activeTab === tab ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600' : 'text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border'
                    }`}
                  >
                    <span>{tab}</span>
                    {activeTab === tab && <Check className="w-4 h-4" />}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

const FilterMenu = ({ dateRange, setDateRange, viewMode, setViewMode, t }: any) => {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="relative">
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className={`p-2.5 rounded-xl border transition-all shadow-sm active:scale-95 ${isOpen ? 'bg-blue-600 text-white border-blue-600' : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg'}`}
        title={t('biomarkers.filters')}
      >
        <Filter className="w-5 h-5" />
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-[60]" onClick={() => setIsOpen(false)} />
          <div className="fixed sm:absolute inset-x-4 sm:inset-x-auto sm:right-0 top-1/2 -translate-y-1/2 sm:top-full sm:translate-y-0 mt-0 sm:mt-3 sm:w-80 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-[2rem] shadow-2xl z-[70] animate-in slide-in-from-top-4 duration-200">
            <div className="flex items-center justify-between p-6 pb-0">
              <h3 className="text-sm font-black text-[#1a2b4b] dark:text-dark-text uppercase tracking-widest">{t('biomarkers.filters')}</h3>
              <button onClick={() => setIsOpen(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
            <div className="p-6 space-y-6">
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">{t('biomarkers.temporal_scope')}</p>
                  <InfoTooltip 
                    className="p-1"
                    content="Time ranges automatically aggregate high-frequency telemetry metrics into optimized buckets (e.g., hourly, daily) while preserving exact measurements for standard clinical data."
                    position="left"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {TIME_RANGES.map(range => (
                    <button key={range.id} onClick={() => setDateRange(range.id)} className={`px-4 py-2 text-xs font-bold rounded-xl border transition-all flex items-center justify-between ${dateRange === range.id ? 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 text-blue-600 dark:text-blue-400' : 'bg-gray-50 dark:bg-dark-bg border-transparent text-gray-500'}`}>
                      <span>{range.longLabel}</span>
                      {dateRange === range.id && <Check className="w-3 h-3" />}
                    </button>
                  ))}
                </div>
              </div>
              <div className="space-y-3">
                <p className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">{t('biomarkers.layout_view')}</p>
                <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl">
                  <button 
                    onClick={() => setViewMode('grid')}
                    className={`flex-1 flex flex-col items-center justify-center py-2 rounded-xl text-[10px] font-black uppercase tracking-tighter transition-all ${viewMode === 'grid' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                  >
                    <Grid className="w-4 h-4 mb-1" />
                    <span>{t('biomarkers.views.grid')}</span>
                  </button>
                  <button 
                    onClick={() => setViewMode('list')}
                    className={`flex-1 flex flex-col items-center justify-center py-2 rounded-xl text-[10px] font-black uppercase tracking-tighter transition-all ${viewMode === 'list' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                  >
                    <List className="w-4 h-4 mb-1" />
                    <span>{t('biomarkers.views.list')}</span>
                  </button>
                  <button 
                    onClick={() => setViewMode('table')}
                    className={`flex-1 flex flex-col items-center justify-center py-2 rounded-xl text-[10px] font-black uppercase tracking-tighter transition-all ${viewMode === 'table' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                  >
                    <TableIcon className="w-4 h-4 mb-1" />
                    <span className="whitespace-nowrap">{t('biomarkers.views.table')}</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

// --- MAIN PAGE COMPONENT ---

function BiomarkerTrends() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const [trendsData, setTrendsData] = useState<any>(null);
  const [activeTab, setActiveTab] = useState('All');
  const [activePerspective, setActivePerspective] = useState<Perspective>('clinical');
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [showAlertsOnly, setShowAlertsOnly] = useState(false);
  const [viewMode, setViewMode] = useState<'grid' | 'list' | 'table'>('table');
  const [chartType, setChartType] = useState<'line' | 'area' | 'bar'>('line');
  const [dateRange, setDateRange] = useState<TimePeriod>('last-12-months');
  const [showGrid, setShowGrid] = useState(false);
  const [showSpikes, setShowSpikes] = useState(true);
  const { showReferenceRanges, setShowReferenceRanges } = useSettingsStore();
  const [isLoading, setIsLoading] = useState(true);
  const loadingRef = useRef(false);
  const [reloadNonce, setReloadNonce] = useState(0);


  const { getTabs, getGroupedData, totalCount, getAbnormal, biomarkers } = useBiomarkers({ trendsData });

  // Facet filters: status / telemetry / unit / source / mapped. Client-mode
  // predicates applied alongside the existing search + alerts filters.
  // Persisted to localStorage so the user's filter selection survives reloads.
  const trendsFilter = useFilterState<BiomarkerObservation>(trendsBiomarkerFacets, {
    storageKey: 'trends-biomarker-filters',
  });


  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  useEffect(() => {
    const loadData = async () => {
      if (loadingRef.current) return;
      loadingRef.current = true;
      
      // 1. Attempt to load from cache immediately
      if (currentPatient?.id) {
        try {
          const cached = await getCachedBiomarkerTrends(currentPatient.id, dateRange);
          if (cached && cached.biomarkers) {
             setTrendsData(cached.biomarkers);
             setIsLoading(false); // We have some data to show
          } else {
             setIsLoading(true);
          }
        } catch (e) {
          console.error("Cache load failed", e);
          setIsLoading(true);
        }
      } else {
        setIsLoading(true);
      }

      try {
        const aggregation = DEFAULT_AGGREGATIONS[dateRange] || '1 day';
        const trends = await getBiomarkerTrends('', '', dateRange, currentPatient?.id, aggregation);

        if (trends && trends.biomarkers) {
          setTrendsData(trends.biomarkers);
        } else {
          setTrendsData(null);
        }
      } catch (err) {
        console.error(err);
      } finally {
        setIsLoading(false);
        loadingRef.current = false;
      }
    };
    loadData();
  }, [currentPatient?.id, dateRange, reloadNonce]);

  const tabs = useMemo(() => getTabs(activePerspective), [getTabs, activePerspective, trendsData]);
  const groupedMarkers = useMemo(() =>
    getGroupedData(activePerspective, activeTab, searchTerm, showAlertsOnly, trendsFilter.isActive ? trendsFilter.matches : undefined),
    [getGroupedData, activePerspective, activeTab, searchTerm, showAlertsOnly, trendsData, trendsFilter.isActive, trendsFilter.matches]
  );

  useEffect(() => {
    if (!tabs.includes(activeTab)) {
      setActiveTab('All');
    }
  }, [tabs, activeTab]);

  const stats = useMemo(() => {
    const visibleTotal = groupedMarkers.reduce((acc, [_, markers]) => acc + markers.length, 0);
    const visibleAlerts = groupedMarkers.reduce((acc, [_, markers]) => {
      const alertsInGroup = markers.filter(m => isAbnormal(m.interpretation)).length;
      return acc + alertsInGroup;
    }, 0);

    return {
      total: activeTab === 'All' && !searchTerm && !showAlertsOnly && !trendsFilter.isActive ? totalCount : visibleTotal,
      alerts: activeTab === 'All' && !searchTerm && !showAlertsOnly && !trendsFilter.isActive ? getAbnormal().length : visibleAlerts
    };
  }, [groupedMarkers, totalCount, getAbnormal, activeTab, searchTerm, showAlertsOnly, trendsFilter.isActive]);

  if (!currentPatient) {
    return <NoPatientState icon={TrendingUp} contextKey="biomarker_trends" />;
  }

  return (
    <div className="max-w-7xl mx-auto pb-10 sm:pb-20">
      <PageHeader
        title={t('biomarkers.title')}
        subtitle={t('biomarkers.subtitle')}
        icon={<TrendingUp className="w-8 h-8" />}
        breadcrumbs={[]}
        showBackButton={true}
      />

      <StickyToolbar
        details={
              <div className="flex flex-wrap items-center gap-4">
                 <div className="flex items-center space-x-2 mr-2">
                    <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('biomarkers.tracked_markers')}:</span>
                    <span className="text-sm font-black text-blue-600">{stats.total}</span>
                 </div>
                 <div className="flex items-center space-x-2 border-r border-gray-100 dark:border-dark-border pr-4">
                    <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('biomarkers.out_of_range')}:</span>
                    <span className={`text-sm font-black ${stats.alerts > 0 ? 'text-red-500' : 'text-gray-900 dark:text-dark-text'}`}>{stats.alerts}</span>
                 </div>

                 <CategoryDropdown 
                    activePerspective={activePerspective} 
                    activeTab={activeTab}
                    tabs={tabs}
                    setActivePerspective={setActivePerspective}
                    setActiveTab={setActiveTab}
                    t={t}
                 />
              </div>

        }
        actions={
          <div className="flex items-center space-x-2">
            <button 
              onClick={() => setShowAlertsOnly(!showAlertsOnly)}
              className={`p-2.5 rounded-xl border transition-all active:scale-95 ${showAlertsOnly ? 'bg-red-50 border-red-200 text-red-600 shadow-sm' : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border text-gray-500 hover:bg-gray-50'}`}
              title={t('biomarkers.toggle_alerts')}
            >
              <AlertCircle className="w-5 h-5" />
            </button>

            <FilterMenu 
               dateRange={dateRange} 
               setDateRange={setDateRange} 
               viewMode={viewMode} 
               setViewMode={setViewMode} 
               t={t} 
            />

            <VisualizationSettings
               chartType={chartType}
               setChartType={setChartType}
               showGrid={showGrid}
               setShowGrid={setShowGrid}
               showSpikes={showSpikes}
               setShowSpikes={setShowSpikes}
               showReferenceRanges={showReferenceRanges}
               setShowReferenceRanges={setShowReferenceRanges}
            />

            <button 
              className="p-2.5 bg-gray-900 text-white rounded-xl font-bold text-sm hover:bg-black transition-all shadow-lg active:scale-95"
              title={t('biomarkers.export')}
            >
              <Download className="w-5 h-5" />
            </button>
            
            <div className="w-px h-6 bg-gray-200 dark:bg-dark-border mx-2" />
            
            <button
              onClick={() => navigate('/catalogs?type=biomarker')}
              className="flex items-center space-x-2 px-5 py-2.5 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
            >
              <ListTree className="w-5 h-5" />
              <span className="hidden xs:inline">{t('biomarkers.catalog_button')}</span>
              <span className="xs:hidden">{t('biomarkers.catalog_button_mobile')}</span>
            </button>
          </div>
        }
      />

      <div className="mb-4">
        <FilterBar<BiomarkerObservation>
          facets={trendsBiomarkerFacets}
          filter={trendsFilter}
          items={biomarkers}
          showActivePills={false}
        />
      </div>

      <BiomarkerList
        isLoading={isLoading}
        groupedData={groupedMarkers}
        viewMode={viewMode}
        perspective={activePerspective}
        searchTerm={searchTerm}
        onSearchChange={setSearchTerm}
        showAlertsOnly={showAlertsOnly}
        onAlertsToggle={setShowAlertsOnly}
        chartType={chartType}
        showGrid={showGrid}
        showSpikes={showSpikes}
        showReferenceRanges={showReferenceRanges}
        initialDataMode="normalized"
        onRemapped={() => setReloadNonce(n => n + 1)}
      />
    </div>
  );
}

export default BiomarkerTrends;
