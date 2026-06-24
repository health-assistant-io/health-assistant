import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { usePatientStore } from '../../store/slices/patientSlice';
import { getBiomarkerTrends } from '../../services/analyticsService';
import CorrelationChart from '../../components/charts/CorrelationChart';
import { PageHeader } from '../../components/ui/PageHeader';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { getStatusColorClass, isAbnormal } from '../../utils/biomarkerUtils';
import { filterBiomarkers } from '../../utils/searchUtils';
import { useLocalStorage } from '../../hooks/useUtils';
import { 
  Activity,
  Calendar, 
  FlaskConical, 
  Heart, 
  Thermometer, 
  Check,
  X,
  Search,
  Info,
  Plus,
  ArrowRight,
  Zap,
  TrendingUp,
  Download,
  Trash2,
  RefreshCcw,
  Sparkles,
  ChevronDown,
  ExternalLink
} from 'lucide-react';

const PREDEFINED_DASHBOARDS = [
  {
    id: 'metabolic',
    name: 'Metabolic Health',
    icon: Activity,
    biomarkers: ['glucose', 'insulin', 'hba1c'],
    color: 'bg-orange-500',
    borderColor: 'border-orange-200'
  },
  {
    id: 'lipids',
    name: 'Lipid Profile',
    icon: Heart,
    biomarkers: ['ldl', 'hdl', 'triglycerides'],
    color: 'bg-red-500',
    borderColor: 'border-red-200'
  },
  {
    id: 'inflammation',
    name: 'Inflammation',
    icon: Thermometer,
    biomarkers: ['crp', 'wbc', 'ferritin'],
    color: 'bg-blue-500',
    borderColor: 'border-blue-200'
  }
];

const DATE_RANGES = [
  { id: 'last-30-days', label: 'Last 30 Days' },
  { id: 'last-90-days', label: 'Last 90 Days' },
  { id: 'last-12-months', label: 'Last 12 Months' },
  { id: 'all-time', label: 'All Time' }
];

const CHART_COLORS = [
  '#3b82f6', // blue
  '#ef4444', // red
  '#10b981', // emerald
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#84cc16', // lime
  '#6366f1', // indigo
  '#f43f5e'  // rose
];

const CorrelativeAnalytics: React.FC = () => {
  const { t } = useTranslation();
  const { currentPatient } = usePatientStore();
  const [trendsData, setTrendsData] = useState<any>(null);
  const [selectedBiomarkers, setSelectedBiomarkers] = useLocalStorage<string[]>('correlative-selected-biomarkers', []);
  const [dateRange, setDateRange] = useState('last-12-months');
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [isDropdownManualOpen, setIsDropdownManualOpen] = useState(false);
  const [hasValidatedInitialMarkers, setHasValidatedInitialMarkers] = useState(false);

  const loadData = useCallback(async () => {
    if (!currentPatient?.id) return;
    setIsLoading(true);
    try {
      const data = await getBiomarkerTrends('', '', dateRange, currentPatient.id);
      setTrendsData(data.biomarkers);
    } catch (error) {
      console.error("Failed to load trends data", error);
    } finally {
      setIsLoading(false);
    }
  }, [currentPatient?.id, dateRange]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Validate stored biomarkers against current patient's available data
  useEffect(() => {
    if (!isLoading && trendsData && !hasValidatedInitialMarkers) {
      const availableSlugs = Object.keys(trendsData);
      const validMarkers = selectedBiomarkers.filter(slug => availableSlugs.includes(slug));
      
      if (validMarkers.length !== selectedBiomarkers.length) {
        setSelectedBiomarkers(validMarkers);
      }
      setHasValidatedInitialMarkers(true);
    }
  }, [isLoading, trendsData, selectedBiomarkers, setSelectedBiomarkers, hasValidatedInitialMarkers]);

  // Reset validation when patient changes
  useEffect(() => {
    setHasValidatedInitialMarkers(false);
  }, [currentPatient?.id]);

  const allAvailableBiomarkers = useMemo(() => {
    if (!trendsData) return [];
    return Object.keys(trendsData).map(key => {
      const history = trendsData[key] || [];
      const latest = history[history.length - 1];
      return {
        slug: key,
        name: latest?.name || key,
        unit: latest?.unit || '',
        category: latest?.technical_category || 'Other',
        latestValue: latest?.value,
        interpretation: latest?.interpretation,
        referenceRange: latest?.referenceRange,
        aliases: latest?.aliases || [],
        definitionId: latest?.biomarker_id || null,
        history: history
      };
    }).sort((a, b) => a.name.localeCompare(b.name));
  }, [trendsData]);

  const filteredBiomarkers = useMemo(() => {
    const unselected = allAvailableBiomarkers.filter(b => !selectedBiomarkers.includes(b.slug));
    return filterBiomarkers(unselected, searchTerm);
  }, [allAvailableBiomarkers, searchTerm, selectedBiomarkers]);

  const chartDatasets = useMemo(() => {
    if (!trendsData) return [];

    return selectedBiomarkers.map((slug, index) => {
      const history = trendsData[slug] || [];
      if (history.length === 0) return null;

      const name = history[0].name || slug;
      const unit = history[0].unit || '';
      
      const values = history.map((p: any) => p.value);
      const minVal = Math.min(...values);
      const maxVal = Math.max(...values);
      const range = maxVal - minVal;

      const data = history.map((p: any) => {
        let normalizedValue = 0;
        if (p.relative_score !== undefined && p.relative_score !== null) {
          normalizedValue = p.relative_score;
        } else if (range > 0) {
          normalizedValue = (p.value - minVal) / range;
        } else {
          normalizedValue = 0.5;
        }

        return {
          date: p.date,
          originalValue: p.value,
          normalizedValue,
          unit
        };
      });

      return {
        label: name,
        data,
        color: CHART_COLORS[index % CHART_COLORS.length],
        unit
      };
    }).filter(Boolean) as any[];
  }, [trendsData, selectedBiomarkers]);

  const toggleBiomarker = (slug: string) => {
    setSelectedBiomarkers((prev: any) => 
      prev.includes(slug) 
        ? prev.filter((s: string) => s !== slug) 
        : [...prev, slug]
    );
  };

  const applyDashboard = (dashboardBiomarkers: string[]) => {
    setSelectedBiomarkers(dashboardBiomarkers);
  };

  const clearWorkspace = () => {
    setSelectedBiomarkers([]);
  };

  // Pearson Correlation calculation for two biomarkers
  const correlationScore = useMemo(() => {
    if (selectedBiomarkers.length !== 2 || !trendsData) return null;
    
    const b1 = trendsData[selectedBiomarkers[0]] || [];
    const b2 = trendsData[selectedBiomarkers[1]] || [];
    
    const map1 = new Map(b1.map((p: any) => [p.date.split('T')[0], p.value]));
    const commonDates = b2.filter((p: any) => map1.has(p.date.split('T')[0]));
    
    if (commonDates.length < 3) return null;

    const x = commonDates.map((p: any) => map1.get(p.date.split('T')[0]) as number);
    const y = commonDates.map((p: any) => p.value);
    
    const n = x.length;
    const sumX = x.reduce((acc: number, val: number) => acc + val, 0);
    const sumY = y.reduce((acc: number, val: number) => acc + val, 0);
    const sumXY = x.reduce((acc: number, val: number, idx: number) => acc + val * y[idx], 0);
    const sumX2 = x.reduce((acc: number, val: number) => acc + val * val, 0);
    const sumY2 = y.reduce((acc: number, val: number) => acc + val * val, 0);
    
    const numerator = (n * sumXY) - (sumX * sumY);
    const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
    
    if (denominator === 0) return 0;
    return numerator / denominator;
  }, [selectedBiomarkers, trendsData]);

  const getCorrelationLabel = (score: number) => {
    const abs = Math.abs(score);
    const sign = score > 0 ? 'Positive' : 'Negative';
    if (abs > 0.8) return `Strong ${sign}`;
    if (abs > 0.5) return `Moderate ${sign}`;
    if (abs > 0.3) return `Weak ${sign}`;
    return 'No significant correlation';
  };

  if (!currentPatient) {
    return <NoPatientState icon={TrendingUp} contextKey="correlative_analytics" />;
  }

  return (
    <div className="max-w-[1600px] mx-auto pb-20 px-4 sm:px-6">
      <PageHeader
        title="Correlative Analytics"
        subtitle="Explore relationships between different biomarkers over time"
        icon={<TrendingUp className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('biomarkers.title'), path: '/analytics/trends' }
        ]}
        showBackButton={true}
      />
      
      <div className="flex flex-col lg:flex-row gap-8 mt-6">
        {/* SIDE PANEL */}
        <aside className="w-full lg:w-96 flex flex-col gap-6 shrink-0">
          
          {/* Active Selections */}
          <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-6 shadow-sm">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-blue-500" />
                <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">
                  {t('common.workspace')}
                </h3>
              </div>
              <div className="flex items-center gap-2">
                {selectedBiomarkers.length > 0 && (
                  <button 
                    onClick={clearWorkspace}
                    className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-all"
                    title="Clear All"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
                <span className="px-2 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[10px] font-black rounded-md">
                  {selectedBiomarkers.length} / 10
                </span>
              </div>
            </div>
            
            <div className="space-y-3 mb-6 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar">
              {selectedBiomarkers.length === 0 ? (
                <div className="py-12 text-center border-2 border-dashed border-gray-100 dark:border-dark-border rounded-3xl group hover:border-blue-200 transition-colors">
                   <Plus className="w-8 h-8 text-gray-200 mx-auto mb-3 group-hover:text-blue-200 transition-colors" />
                   <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-6">
                     Add biomarkers to visualize correlations
                   </p>
                </div>
              ) : (
                selectedBiomarkers.map((slug, idx) => {
                  const marker = allAvailableBiomarkers.find(b => b.slug === slug);
                  const color = CHART_COLORS[idx % CHART_COLORS.length];
                  
                  return (
                    <div key={slug} className={`group relative flex items-center justify-between p-4 rounded-2xl border transition-all ${
                      marker?.interpretation && isAbnormal(marker.interpretation) 
                        ? 'bg-red-50/50 dark:bg-red-900/10 border-red-100 dark:border-red-900/20' 
                        : 'bg-gray-50 dark:bg-dark-bg border-transparent hover:border-gray-200 dark:hover:border-dark-border'
                    }`}>
                      <div className="flex items-center gap-3">
                        <div className={`w-1.5 h-10 rounded-full ${marker?.interpretation && isAbnormal(marker.interpretation) ? 'animate-pulse' : ''}`} style={{ backgroundColor: color }} />
                        <div>
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate max-w-[140px]">{marker?.name || slug}</p>
                            {marker?.interpretation && isAbnormal(marker.interpretation) && (
                              <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse shrink-0" />
                            )}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[10px] font-black text-gray-400 uppercase tracking-tighter shrink-0">
                              {marker?.latestValue} {marker?.unit}
                            </span>
                            {marker?.interpretation && (
                              <span className={`px-1.5 py-0.5 text-[8px] font-black rounded uppercase border scale-90 ${getStatusColorClass(marker.interpretation)}`}>
                                {marker.interpretation}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <button 
                          onClick={() => {
                            const targetId = marker?.definitionId;
                            if (targetId) window.open(`/biomarkers/details/${targetId}`, '_blank');
                          }}
                          className="p-1.5 text-gray-300 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg transition-all"
                          title="Open Details"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </button>
                        <button 
                          onClick={() => toggleBiomarker(slug)}
                          className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-all"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {/* Quick Add Search */}
            <div className="relative">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input 
                type="text" 
                placeholder={t('common.search_to_add')}
                className="w-full pl-11 pr-12 py-3.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-2xl text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-none transition-all placeholder:text-gray-400"
                value={searchTerm}
                onChange={(e) => {
                  setSearchTerm(e.target.value);
                  if (e.target.value) setIsDropdownManualOpen(true);
                }}
                onFocus={() => setIsDropdownManualOpen(true)}
              />
              <button 
                onClick={() => setIsDropdownManualOpen(!isDropdownManualOpen)}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 hover:bg-gray-200 dark:hover:bg-dark-surface rounded-lg transition-colors text-gray-400"
              >
                <ChevronDown className={`w-4 h-4 transition-transform duration-200 ${isDropdownManualOpen ? 'rotate-180' : ''}`} />
              </button>
              
              {(searchTerm || isDropdownManualOpen) && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setIsDropdownManualOpen(false)} />
                  <div className="absolute top-full left-0 right-0 mt-3 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-[2rem] shadow-2xl z-50 overflow-hidden max-h-80 overflow-y-auto animate-in fade-in slide-in-from-top-2">
                    <div className="p-3 bg-gray-50 dark:bg-dark-bg border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
                       <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest ml-2">Available Results</span>
                       <button onClick={() => { setSearchTerm(''); setIsDropdownManualOpen(false); }}><X className="w-3 h-3 text-gray-400" /></button>
                    </div>
                    {filteredBiomarkers.length === 0 ? (
                      <div className="p-8 text-center">
                         <p className="text-xs font-bold text-gray-400 uppercase">No matching markers</p>
                      </div>
                    ) : (
                      filteredBiomarkers.map(b => (
                        <button 
                          key={b.slug}
                          onClick={() => { toggleBiomarker(b.slug); setSearchTerm(''); setIsDropdownManualOpen(false); }}
                          className="w-full flex items-center justify-between p-4 hover:bg-blue-50 dark:hover:bg-blue-900/10 text-left border-b border-gray-50 dark:border-dark-border last:border-none group"
                        >
                          <div>
                            <p className="text-sm font-bold text-gray-900 dark:text-dark-text group-hover:text-blue-600 transition-colors">{b.name}</p>
                            <div className="flex items-center gap-2 mt-0.5">
                              <span className="text-[9px] font-black text-blue-500 uppercase px-1.5 bg-blue-50 dark:bg-blue-900/30 rounded-sm">{b.category}</span>
                              <span className="text-[9px] font-bold text-gray-400 uppercase">{b.latestValue} {b.unit}</span>
                            </div>
                          </div>
                          <Plus className="w-4 h-4 text-gray-300 group-hover:text-blue-500 group-hover:scale-125 transition-all" />
                        </button>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Smart Templates */}
          <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-6 shadow-sm flex-1">
             <div className="flex items-center justify-between mb-6">
               <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-widest flex items-center gap-2">
                  <Zap className="w-4 h-4 text-amber-500" />
                  {t('common.smart_templates')}
               </h3>
               <Sparkles className="w-4 h-4 text-purple-400" />
             </div>
             
             <div className="flex flex-col gap-4">
                {PREDEFINED_DASHBOARDS.map((db) => {
                  const isActive = JSON.stringify(selectedBiomarkers.sort()) === JSON.stringify([...db.biomarkers].sort());
                  const Icon = db.icon;
                  return (
                    <button
                      key={db.id}
                      onClick={() => applyDashboard(db.biomarkers)}
                      className={`relative flex items-center gap-4 p-4 rounded-3xl border transition-all text-left group overflow-hidden ${
                        isActive 
                          ? 'bg-blue-50 dark:bg-blue-900/10 border-blue-200' 
                          : 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border hover:border-gray-200'
                      }`}
                    >
                      <div className={`p-3 rounded-2xl text-white ${db.color} shadow-lg shadow-current/20 z-10 group-hover:scale-110 transition-transform`}>
                        <Icon className="w-4 h-4" />
                      </div>
                      <div className="z-10">
                        <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-wider">{db.name}</p>
                        <div className="flex gap-1.5 mt-1">
                          {db.biomarkers.slice(0, 3).map(b => (
                            <span key={b} className="text-[9px] font-black text-gray-400 uppercase">{b}</span>
                          ))}
                        </div>
                      </div>
                      <div className="ml-auto flex items-center">
                        {isActive ? (
                          <div className="p-1 bg-blue-500 rounded-full">
                            <Check className="w-3 h-3 text-white" />
                          </div>
                        ) : (
                          <ArrowRight className="w-4 h-4 text-gray-300 transition-transform group-hover:translate-x-1" />
                        )}
                      </div>
                    </button>
                  );
                })}
             </div>
          </div>
        </aside>

        {/* MAIN CONTENT AREA */}
        <div className="flex-1 min-w-0 flex flex-col gap-6">
          {/* Main Chart Card */}
          <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-8 shadow-sm flex flex-col h-full">
            <div className="flex flex-col md:flex-row justify-between items-start gap-6 mb-10">
              <div>
                <div className="flex items-center gap-3 mb-2">
                  <h2 className="text-3xl font-black text-[#1a2b4b] dark:text-dark-text tracking-tight">
                    Correlative Visualizer
                  </h2>
                  <div className="px-2 py-0.5 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 text-[9px] font-black uppercase rounded border border-indigo-100 dark:border-indigo-800">
                    High-Res
                  </div>
                </div>
                
                <div className="flex flex-wrap items-center gap-4">
                  <div className="flex items-center gap-2 text-sm text-gray-500 font-medium bg-gray-50 dark:bg-dark-bg px-4 py-1.5 rounded-full border border-gray-100 dark:border-dark-border">
                    <Calendar className="w-4 h-4 text-blue-500" />
                    <span className="text-xs uppercase font-black tracking-widest text-gray-400 mr-2">Timeline</span>
                    <select 
                      value={dateRange}
                      onChange={(e) => setDateRange(e.target.value)}
                      className="bg-transparent border-none text-blue-600 font-black text-xs p-0 focus:ring-0 cursor-pointer uppercase tracking-widest"
                    >
                      {DATE_RANGES.map(range => (
                        <option key={range.id} value={range.id}>{range.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="h-4 w-px bg-gray-200 dark:bg-dark-border hidden md:block" />
                  <div className="flex items-center gap-2 text-[10px] font-black text-emerald-600 uppercase tracking-widest bg-emerald-50 dark:bg-emerald-900/30 px-4 py-1.5 rounded-full border border-emerald-100 dark:border-emerald-800">
                    <Activity className="w-3 h-3" />
                    Feature: Normalized Scale
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                 <button 
                  onClick={() => window.print()}
                  className="p-3 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-dark-bg rounded-2xl transition-all border border-gray-100 dark:border-dark-border group"
                  title="Print Analysis"
                 >
                    <Download className="w-5 h-5 group-hover:scale-110 transition-transform" />
                 </button>
                 <button 
                  onClick={loadData}
                  className="p-3 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-dark-bg rounded-2xl transition-all border border-gray-100 dark:border-dark-border group"
                  title="Reload Data"
                 >
                    <RefreshCcw className="w-5 h-5 group-hover:rotate-180 transition-transform duration-500" />
                 </button>
              </div>
            </div>

            {isLoading ? (
              <div className="flex-1 flex flex-col items-center justify-center py-40">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-6"></div>
                <p className="text-gray-400 font-black uppercase tracking-[0.3em] text-[10px]">Synchronizing clinical data history...</p>
              </div>
            ) : selectedBiomarkers.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center py-40 text-center">
                <div className="w-32 h-32 bg-gray-50 dark:bg-dark-bg rounded-[3rem] flex items-center justify-center mb-8 border-2 border-dashed border-gray-200 dark:border-dark-border relative group">
                  <div className="absolute inset-0 bg-blue-500/5 rounded-[3rem] scale-0 group-hover:scale-100 transition-transform duration-500" />
                  <Activity className="w-12 h-12 text-gray-300 group-hover:text-blue-400 transition-colors" />
                </div>
                <h3 className="text-2xl font-black text-gray-900 dark:text-dark-text tracking-tight uppercase mb-3">Workspace Empty</h3>
                <p className="text-gray-400 max-w-sm mx-auto font-bold text-sm leading-relaxed uppercase tracking-tighter">
                  Please select biomarkers from the left side panel to start your cross-temporal analysis.
                </p>
              </div>
            ) : (
              <div className="flex-1 min-h-[500px]">
                <CorrelationChart datasets={chartDatasets} height={600} />
              </div>
            )}
          </div>

          {/* Intelligent Insights Footer */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
             {correlationScore !== null ? (
               <div className="bg-gradient-to-br from-[#1a2b4b] to-[#2c3e50] rounded-[2.5rem] p-10 text-white shadow-2xl shadow-blue-900/20 relative overflow-hidden group">
                  <div className="absolute top-0 right-0 p-10 opacity-5 group-hover:opacity-10 transition-opacity">
                    <TrendingUp className="w-48 h-48" />
                  </div>
                  <div className="relative z-10">
                    <div className="flex items-center gap-3 mb-6">
                      <div className="w-8 h-8 bg-blue-500/20 rounded-lg flex items-center justify-center border border-blue-500/30">
                        <Sparkles className="w-4 h-4 text-blue-400" />
                      </div>
                      <p className="text-[10px] font-black uppercase tracking-[0.4em] text-blue-400">Correlation Engine v1.0</p>
                    </div>
                    
                    <div className="mb-8">
                      <div className="flex items-end gap-3 mb-2">
                        <h4 className="text-5xl font-black tracking-tighter">
                          {correlationScore.toFixed(3)}
                        </h4>
                        <div className="mb-2">
                          <p className="text-gray-400 font-black uppercase text-[10px] tracking-widest leading-none">
                            Pearson Coefficient
                          </p>
                        </div>
                      </div>
                      
                      {/* Strength Meter */}
                      <div className="w-full h-2 bg-white/10 rounded-full mt-6 relative">
                        {/* Center Line */}
                        <div className="absolute left-1/2 top-[-4px] bottom-[-4px] w-0.5 bg-white/30 z-20" />
                        
                        {/* The Bar */}
                        <div 
                          className={`absolute top-0 bottom-0 transition-all duration-1000 rounded-full ${
                            correlationScore < 0 ? 'bg-orange-500 right-1/2' : 'bg-blue-500 left-1/2'
                          }`}
                          style={{ 
                            width: `${Math.abs(correlationScore) * 50}%`,
                          }}
                        />
                      </div>
                      <div className="flex justify-between mt-3 text-[9px] font-black text-gray-500 uppercase tracking-widest">
                        <div className="flex flex-col items-start">
                          <span>Inverse</span>
                          <span className="text-orange-500/50">-1.0</span>
                        </div>
                        <div className="flex flex-col items-center">
                          <span>Neutral</span>
                          <span>0.0</span>
                        </div>
                        <div className="flex flex-col items-end">
                          <span>Direct</span>
                          <span className="text-blue-500/50">+1.0</span>
                        </div>
                      </div>
                    </div>

                    <div className="flex flex-col gap-4">
                       <div className={`inline-flex items-center gap-3 px-6 py-3 rounded-2xl border font-black uppercase text-xs tracking-widest w-fit ${
                         Math.abs(correlationScore) > 0.6 ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' : 'bg-blue-500/10 border-blue-500/30 text-blue-400'
                       }`}>
                          {getCorrelationLabel(correlationScore)}
                       </div>
                       
                       <div className="flex items-start gap-4 p-5 bg-white/5 rounded-[1.5rem] backdrop-blur-md border border-white/10 mt-2">
                        <Info className="w-5 h-5 text-blue-400 shrink-0 mt-0.5" />
                        <div className="space-y-2">
                          <p className="text-xs text-blue-50/70 leading-relaxed font-bold">
                            This statistical analysis examines the linear relationship between {selectedBiomarkers[0].toUpperCase()} and {selectedBiomarkers[1].toUpperCase()}. 
                            A score closer to ±1 indicates a predictable co-movement pattern.
                          </p>
                          <div className="pt-2 border-t border-white/5">
                            <button 
                              onClick={() => {
                                navigator.clipboard.writeText(`Health Assistant Correlation Index: ${correlationScore.toFixed(3)} (${getCorrelationLabel(correlationScore)}) for ${selectedBiomarkers[0]} vs ${selectedBiomarkers[1]}`);
                              }}
                              className="text-[10px] text-blue-400 hover:text-blue-300 font-black uppercase tracking-[0.2em] flex items-center gap-1.5 transition-colors"
                            >
                              <Plus className="w-3 h-3 rotate-45" /> {/* Using Plus as a placeholder for copy if clipboard icon not available, but let's use a standard string */}
                              Copy Index result
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
               </div>
             ) : selectedBiomarkers.length > 2 ? (
               <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-10 flex flex-col justify-center shadow-sm">
                  <div className="flex items-center gap-3 mb-4">
                    <Activity className="w-5 h-5 text-indigo-500" />
                    <h3 className="text-sm font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">Multi-Variate Mode</h3>
                  </div>
                  <p className="text-gray-500 font-medium text-sm leading-relaxed mb-6">
                    You have {selectedBiomarkers.length} biomarkers selected. Correlation scores are currently limited to dual-variable comparisons. Remove markers until only two remain to re-enable statistical indexing.
                  </p>
                  <div className="flex items-center gap-2">
                     <div className="px-3 py-1 bg-gray-50 dark:bg-dark-bg rounded-lg border border-gray-100 dark:border-dark-border text-[10px] font-black text-gray-400 uppercase">Tip: Select only 2 for deep analysis</div>
                  </div>
               </div>
             ) : (
               <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-10 flex flex-col justify-center items-center text-center shadow-sm opacity-60">
                  <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-2xl flex items-center justify-center mb-4">
                    <Sparkles className="w-8 h-8 text-gray-200" />
                  </div>
                  <h3 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.2em]">Select 2 biomarkers for statistical indexing</h3>
               </div>
             )}
             
             <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-10 shadow-sm">
                <h3 className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-widest mb-8 flex items-center gap-2">
                  <Activity className="w-4 h-4 text-green-500" />
                  Clinical Methodology
                </h3>
                <ul className="space-y-6">
                   <li className="flex gap-5 group">
                      <div className="w-2 h-2 rounded-full bg-blue-500 mt-2 shrink-0 group-hover:scale-150 transition-transform" />
                      <div>
                        <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-wider mb-1">Normalization Algorithm</p>
                        <p className="text-xs text-gray-500 dark:text-dark-muted font-bold leading-relaxed">
                          Values are mapped to a [0, 1] interval based on the temporal min/max. 100% represents the highest peak in the selected range.
                        </p>
                      </div>
                   </li>
                   <li className="flex gap-5 group">
                      <div className="w-2 h-2 rounded-full bg-amber-500 mt-2 shrink-0 group-hover:scale-150 transition-transform" />
                      <div>
                        <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-wider mb-1">Synchronized Sampling</p>
                        <p className="text-xs text-gray-500 dark:text-dark-muted font-bold leading-relaxed">
                          Visualizer aligns disparate lab results onto a unified daily timeline. Empty dates between measurements are bridged using linear interpolation.
                        </p>
                      </div>
                   </li>
                </ul>
             </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CorrelativeAnalytics;
