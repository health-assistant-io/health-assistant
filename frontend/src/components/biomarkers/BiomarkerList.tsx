import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  Info, ChevronRight, Search, X, AlertCircle, Trash2,
  ArrowUpDown, ArrowUp, ArrowDown, ArrowUpCircle, ArrowDownCircle, CheckCircle2, Activity
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import LineChart from '../charts/LineChart';
import { BiomarkerObservation } from '../../types/biomarker';
import { getStatusColorClass, isAbnormal, formatUnit, formatBiomarkerValue } from '../../utils/biomarkerUtils';
import { Perspective } from '../../hooks/useBiomarkers';
import { useBiomarkerPrecisionProfile } from '../../hooks/useBiomarkerPrecision';
import { UnmappedBiomarkerMenu } from './UnmappedBiomarkerMenu';

interface BiomarkerListProps {
  biomarkers?: BiomarkerObservation[];
  groupedData?: [string, BiomarkerObservation[]][];
  viewMode?: 'grid' | 'list' | 'table';
  compact?: boolean;
  showCharts?: boolean;
  perspective?: Perspective;
  searchTerm?: string;
  onSearchChange?: (term: string) => void;
  showAlertsOnly?: boolean;
  onAlertsToggle?: (show: boolean) => void;
  chartType?: 'line' | 'area' | 'bar';
  showGrid?: boolean;
  showSpikes?: boolean;
  showReferenceRanges?: boolean;
  showDate?: boolean;
  showSource?: boolean;
  initialDataMode?: 'raw' | 'normalized';
  isLoading?: boolean;
  emptyMessage?: string;
  emptySubtitle?: string;
  onDelete?: (id: string) => void;
  dataMode?: 'raw' | 'normalized';
  onDataModeChange?: (mode: 'raw' | 'normalized') => void;
  hideDataModeToggle?: boolean;
  onRemapped?: () => void;
}

/**
 * Modular component to display biomarkers in Grid, List, or Smart Table modes.
 */
export const BiomarkerList = React.memo(({
  biomarkers = [],
  groupedData = [],
  viewMode = 'grid',
  compact = false,
  showCharts = true,
  perspective = 'clinical',
  searchTerm = '',
  onSearchChange,
  showAlertsOnly = false,
  onAlertsToggle,
  chartType = 'line',
  showGrid = false,
  showSpikes = false,
  showReferenceRanges = true,
  showDate = true,
  showSource = true,
  initialDataMode = 'normalized',
  isLoading = false,
  emptyMessage,
  emptySubtitle,
  onDelete,
  dataMode: dataModeProp,
  onDataModeChange: onDataModeChangeProp,
  hideDataModeToggle = false,
  onRemapped,
}: BiomarkerListProps) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const [selectedInfo, setSelectedInfo] = useState<BiomarkerObservation | null>(null);
  const [internalDataMode, setInternalDataMode] = useState<'raw' | 'normalized'>(initialDataMode);
  
  const activeDataMode = dataModeProp !== undefined ? dataModeProp : internalDataMode;
  const handleDataModeChange = (mode: 'raw' | 'normalized') => {
    if (onDataModeChangeProp) onDataModeChangeProp(mode);
    else setInternalDataMode(mode);
  };

  const [sortConfig, setSortConfig] = useState<{ key: string; direction: 'asc' | 'desc' } | null>({
    key: 'status',
    direction: 'desc'
  });

  // Helper to resolve active data based on mode
  const getActiveData = (marker: BiomarkerObservation) => {
    if (activeDataMode === 'raw') {
      return {
        value: marker.value.raw,
        unit: marker.unit.rawSymbol,
        referenceRange: marker.referenceRange.raw || marker.referenceRange,
        isNormalized: false
      };
    }
    return {
      value: marker.value.normalized ?? marker.value.raw,
      unit: marker.unit.normalizedSymbol || marker.unit.rawSymbol,
      referenceRange: marker.referenceRange.standard || marker.referenceRange,
      isNormalized: true
    };
  };

  // Smart Table sorting logic
  const sortedGroupedData = useMemo(() => {
    if (viewMode !== 'table' || !sortConfig) return groupedData;

    return groupedData.map(([groupName, markers]) => {
      const sortedMarkers = [...markers].sort((a, b) => {
        let aValue: any;
        let bValue: any;

        const aData = getActiveData(a);
        const bData = getActiveData(b);

        // Specialized sorting for different keys
        if (sortConfig.key === 'status') {
          aValue = isAbnormal(a.interpretation) ? 1 : 0;
          bValue = isAbnormal(b.interpretation) ? 1 : 0;
        } else if (sortConfig.key === 'displayName') {
          aValue = a.displayName.toLowerCase();
          bValue = b.displayName.toLowerCase();
        } else if (sortConfig.key === 'value') {
          aValue = aData.value;
          bValue = bData.value;
        } else if (sortConfig.key === 'date') {
          aValue = new Date(a.source.date).getTime();
          bValue = new Date(b.source.date).getTime();
        } else {
          aValue = a[sortConfig.key as keyof BiomarkerObservation];
          bValue = b[sortConfig.key as keyof BiomarkerObservation];
        }

        if (aValue < bValue) return sortConfig.direction === 'asc' ? -1 : 1;
        if (aValue > bValue) return sortConfig.direction === 'asc' ? 1 : -1;
        return 0;
      });
      return [groupName, sortedMarkers] as [string, BiomarkerObservation[]];
    });
  }, [groupedData, viewMode, sortConfig, activeDataMode]);

  const requestSort = (key: string) => {
    let direction: 'asc' | 'desc' = 'asc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc';
    }
    setSortConfig({ key, direction });
  };

  const renderSortIcon = (key: string) => {
    if (sortConfig?.key !== key) return <ArrowUpDown className="w-3 h-3 ml-1 opacity-30" />;
    return sortConfig.direction === 'asc' ? <ArrowUp className="w-3 h-3 ml-1" /> : <ArrowDown className="w-3 h-3 ml-1" />;
  };

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-40">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
        <p className="text-gray-500 animate-pulse font-medium">{t('biomarkers.loading_trends')}</p>
      </div>
    );
  }

  if (groupedData.length === 0) {
    return (
      <div className="col-span-full bg-white dark:bg-dark-surface rounded-3xl border-2 border-dashed border-gray-200 dark:border-dark-border py-20 flex flex-col items-center justify-center text-center p-6 animate-in fade-in duration-500">
        <div className="w-20 h-20 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mb-6">
          <Search className="w-10 h-10 text-gray-300" />
        </div>
        <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">
          {emptyMessage || t('biomarkers.no_results')}
        </h3>
        <p className="text-gray-500 dark:text-dark-muted mt-2 max-w-sm">
          {emptySubtitle || t('biomarkers.no_results_subtitle')}
        </p>
        {(searchTerm || showAlertsOnly) && (
          <button 
            onClick={() => {
              if (onSearchChange) onSearchChange('');
              if (onAlertsToggle) onAlertsToggle(false);
            }}
            className="mt-8 px-6 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
          >
            {t('biomarkers.clear_filters')}
          </button>
        )}
      </div>
    );
  }

  const renderGridCard = (marker: BiomarkerObservation) => {
    const active = getActiveData(marker);
    const history = marker._rawJson?.history || [];
    
    // For trends/history we use the appropriate values
    const dataPts = history.slice(-10).map((d: any) => ({ 
      name: new Date(d.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), 
      value: activeDataMode === 'raw' ? (d.raw_value || d.value) : d.value,
      min_value: d.min_value,
      max_value: d.max_value,
      range: (d.min_value !== undefined && d.max_value !== undefined) ? [d.min_value, d.max_value] : undefined
    }));

    let change = 0;
    if (history.length > 1) {
      const first = history[0];
      const current = history[history.length - 1];
      const firstVal = activeDataMode === 'raw' ? (first.raw_value || first.value) : first.value;
      const currentVal = activeDataMode === 'raw' ? (current.raw_value || current.value) : current.value;
      
      if (firstVal && firstVal !== 0) {
        change = ((currentVal - firstVal) / firstVal) * 100;
      }
    }
    const changeStr = change > 0 ? `+${change.toFixed(1)}%` : `${change.toFixed(1)}%`;
    const changeColor = change > 0 ? 'text-red-500' : (change < 0 ? 'text-green-500' : 'text-gray-500');
    
    // UI Logic for icons and value colors
    const status = marker.interpretation.toLowerCase();
    const isHigh = status.includes('high') || status === 'h';
    const isLow = status.includes('low') || status === 'l';
    const isNormal = !isHigh && !isLow;

    const valueColorClass = isHigh ? 'text-red-600' : (isLow ? 'text-blue-600' : 'text-gray-900 dark:text-dark-text');
    const targetId = marker.definitionId;
    const isNavigable = !!targetId;

    return (
      <div 
        key={marker.id} 
        className={`bg-white dark:bg-dark-surface shadow-sm border border-gray-100 dark:border-dark-border hover:shadow-xl transition-all flex flex-col group relative ${
          compact ? 'rounded-2xl p-4' : 'rounded-3xl p-5 sm:p-6'
        } ${
          showCharts ? 'min-h-[300px] sm:min-h-[340px]' : 'min-h-fit'
        }`}
      >
        <div className={`flex justify-between items-start ${isNavigable ? 'cursor-pointer' : ''} ${compact ? 'mb-2' : 'mb-4'}`} onClick={() => isNavigable && navigate(`/biomarkers/details/${targetId}`)}>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center">
              <h3 className={`${compact ? 'text-base' : 'text-lg sm:text-xl'} font-black text-[#1a2b4b] dark:text-dark-text ${isNavigable ? 'group-hover:text-blue-600' : ''} transition-colors leading-tight mr-2`}>{marker.displayName}</h3>
              {marker.isTelemetry && (
                <div className="flex items-center justify-center p-1 mr-1 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-500 rounded-md" title="Telemetry/IoT Data">
                  <Activity className="w-3.5 h-3.5" />
                </div>
              )}
              {marker.isUnmapped && (
                <UnmappedBiomarkerMenu rawName={marker.displayName} onRemapped={onRemapped} />
              )}
              {marker.info && (
                <button 
                  type="button"
                  onClick={(e) => { 
                    e.preventDefault();
                    e.stopPropagation(); 
                    setSelectedInfo(marker); 
                  }}
                  className="p-1 text-blue-400 transition-colors hover:text-blue-600 relative z-30"
                >
                  <Info className="w-4 h-4" />
                </button>
              )}
              {onDelete && (
                <button 
                  type="button"
                  onClick={(e) => { 
                    e.preventDefault();
                    e.stopPropagation(); 
                    onDelete(marker.id); 
                  }}
                  className="p-1 text-gray-300 transition-colors hover:text-red-500 relative z-30 ml-1"
                  title={t('common.delete')}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
            <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest mt-1 flex items-center flex-wrap gap-y-1">
              {showReferenceRanges && (
                <span className={`mr-2 font-mono font-black text-blue-600/80 dark:text-blue-400/80 ${compact ? 'text-[10px]' : 'text-[11px]'}`}>
                  {active.referenceRange.displayText}
                </span>
              )}
              <span className="opacity-80">{formatUnit(active.unit)}</span>
            </p>
          </div>
          
          <div className="flex flex-col items-end shrink-0 ml-4">
            {isHigh ? (
              <div className={`${compact ? 'p-1.5' : 'p-2'} bg-red-50 dark:bg-red-900/20 rounded-2xl shadow-inner animate-pulse`}>
                <ArrowUpCircle className={`${compact ? 'w-5 h-5' : 'w-6 h-6'} text-red-500 shadow-[0_0_12px_rgba(239,68,68,0.3)]`} />
              </div>
            ) : isLow ? (
              <div className={`${compact ? 'p-1.5' : 'p-2'} bg-blue-50 dark:bg-blue-900/20 rounded-2xl shadow-inner animate-pulse`}>
                <ArrowDownCircle className={`${compact ? 'w-5 h-5' : 'w-6 h-6'} text-blue-500 shadow-[0_0_12px_rgba(59,130,246,0.3)]`} />
              </div>
            ) : (
              <div className={`${compact ? 'p-1.5' : 'p-2'} bg-green-50/30 dark:bg-green-900/10 rounded-2xl`}>
                <CheckCircle2 className={`${compact ? 'w-5 h-5' : 'w-6 h-6'} text-green-300 dark:text-green-800/40`} />
              </div>
            )}
            <span className={`mt-1 text-[8px] font-black uppercase tracking-tighter ${isHigh ? 'text-red-500' : isLow ? 'text-blue-500' : 'text-gray-300'}`}>
              {marker.interpretation}
            </span>
          </div>
        </div>
        
        <div className={`flex items-baseline justify-between ${compact ? 'mb-0' : 'mb-4'}`}>
          <span className={`${compact ? 'text-lg sm:text-xl' : 'text-xl sm:text-2xl'} font-black tracking-tight ${valueColorClass}`}>
            {formatBiomarkerValue(active.value, precisionProfile)}
          </span>
          {showCharts && history.length > 1 && (
            <div className={`flex items-center text-[10px] font-bold ${change > 0 ? 'text-red-500' : 'text-green-500'} opacity-80`}>
              {change > 0 ? <ArrowUp className="w-2.5 h-2.5 mr-0.5" /> : <ArrowDown className="w-2.5 h-2.5 mr-0.5" />}
              {changeStr}
            </div>
          )}
        </div>

        {showCharts ? (
          <div className="flex-1 min-h-[140px] sm:min-h-[180px] nodrag mt-2">
            <LineChart 
              data={dataPts} 
              height="100%" 
              color={isNormal ? '#3b82f6' : (isHigh ? '#ef4444' : '#3b82f6')} 
              referenceRange={{
                min: active.referenceRange.min,
                max: active.referenceRange.max
              }}
              showReferenceLines={showReferenceRanges}
              chartType={chartType}
              showGrid={showGrid}
              showSpikes={showSpikes}
              unit={formatUnit(active.unit)}
            />
          </div>
        ) : (
          (showDate || (showSource && marker.source.filename)) && (
            <div className={`mt-auto pt-4 border-t border-gray-50 dark:border-dark-border flex items-center ${(showDate && showSource && marker.source.filename) ? 'justify-between' : 'justify-end'}`}>
              {showDate && (
                <div className="flex flex-col">
                  <span className="text-[9px] font-black text-gray-300 uppercase tracking-widest">{t('common.date')}</span>
                  <span className="text-xs font-bold text-gray-700 dark:text-dark-text">{new Date(marker.source.date).toLocaleDateString()}</span>
                </div>
              )}
              {showSource && marker.source.filename && (
                <div className="flex flex-col text-right">
                  <span className="text-[9px] font-black text-gray-300 uppercase tracking-widest">{t('common.source')}</span>
                  <span className="text-xs font-bold text-gray-700 dark:text-dark-text truncate max-w-[120px]">{marker.source.filename}</span>
                </div>
              )}
            </div>
          )
        )}
      </div>
    );
  };

  const renderListItem = (marker: BiomarkerObservation) => {
    const active = getActiveData(marker);
    const history = marker._rawJson?.history || [];
    const dataPts = history.slice(-10).map((d: any) => ({ 
      name: new Date(d.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), 
      value: activeDataMode === 'raw' ? (d.raw_value || d.value) : d.value,
      min_value: d.min_value,
      max_value: d.max_value,
      range: (d.min_value !== undefined && d.max_value !== undefined) ? [d.min_value, d.max_value] : undefined
    }));

    let change = 0;
    if (history.length > 1) {
      const first = history[0];
      const current = history[history.length - 1];
      const firstVal = activeDataMode === 'raw' ? (first.raw_value || first.value) : first.value;
      const currentVal = activeDataMode === 'raw' ? (current.raw_value || current.value) : current.value;
      
      if (firstVal && firstVal !== 0) {
        change = ((currentVal - firstVal) / firstVal) * 100;
      }
    }
    const changeStr = change > 0 ? `+${change.toFixed(1)}%` : `${change.toFixed(1)}%`;
    
    // UI Logic for icons and value colors
    const status = marker.interpretation.toLowerCase();
    const isHigh = status.includes('high') || status === 'h';
    const isLow = status.includes('low') || status === 'l';

    const valueColorClass = isHigh ? 'text-red-600' : (isLow ? 'text-blue-600' : 'text-gray-900 dark:text-dark-text');
    const targetId = marker.definitionId;
    const isNavigable = !!targetId;

    return (
      <div key={marker.id} className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-4 flex flex-col sm:flex-row items-center space-y-4 sm:space-y-0 sm:space-x-8 hover:shadow-md transition-all group relative">
        <div className={`w-full sm:w-[250px] flex items-center shrink-0 ${isNavigable ? 'cursor-pointer' : ''}`} onClick={() => isNavigable && navigate(`/biomarkers/details/${targetId}`)}>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center">
              <h3 className={`font-bold text-gray-900 dark:text-dark-text truncate ${isNavigable ? 'hover:text-blue-600' : ''} leading-tight mr-2`}>{marker.displayName}</h3>
              {marker.isTelemetry && (
                <div className="flex items-center justify-center p-1 mr-1 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-500 rounded-md" title="Telemetry/IoT Data">
                  <Activity className="w-3.5 h-3.5" />
                </div>
              )}
              {marker.isUnmapped && (
                <UnmappedBiomarkerMenu rawName={marker.displayName} onRemapped={onRemapped} />
              )}
              {marker.info && (
                <button 
                  type="button"
                  onClick={(e) => { 
                    e.preventDefault();
                    e.stopPropagation(); 
                    setSelectedInfo(marker); 
                  }}
                  className="p-1 text-blue-400 transition-colors hover:text-blue-600 shrink-0"
                >
                  <Info className="w-4 h-4" />
                </button>
              )}
              {onDelete && (
                <button 
                  type="button"
                  onClick={(e) => { 
                    e.preventDefault();
                    e.stopPropagation(); 
                    onDelete(marker.id); 
                  }}
                  className="p-1 text-gray-300 transition-colors hover:text-red-500 shrink-0 ml-1"
                  title={t('common.delete')}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </div>
        
        <div className="w-full sm:w-24 shrink-0 flex items-center">
           {isHigh ? (
              <ArrowUpCircle className="w-5 h-5 text-red-500 mr-2" />
            ) : isLow ? (
              <ArrowDownCircle className="w-5 h-5 text-blue-500 mr-2" />
            ) : (
              <CheckCircle2 className="w-5 h-5 text-green-200 dark:text-green-800/30 mr-2" />
            )}
           <span className={`text-[10px] font-bold uppercase tracking-wider ${isHigh ? 'text-red-600' : isLow ? 'text-blue-600' : 'text-gray-400'}`}>
              {marker.interpretation}
           </span>
        </div>

        <div className="w-full sm:w-32 shrink-0">
          <div className="flex items-baseline space-x-1">
             <span className={`text-xl font-black ${valueColorClass}`}>{formatBiomarkerValue(active.value, precisionProfile)}</span>
              <span className="text-[10px] font-bold text-gray-400">{formatUnit(active.unit)}</span>
          </div>
        </div>

        <div className="w-full sm:w-40 shrink-0">
          {showReferenceRanges && (
            <div className="flex flex-col">
              <span className="text-[9px] font-black text-gray-300 uppercase tracking-widest">{t('examination_detail.biomarkers.table.ref_range')}</span>
              <span className="text-[10px] font-mono font-bold text-blue-500/70 dark:text-blue-400/60 leading-tight truncate">
                {active.referenceRange.displayText}
              </span>
            </div>
          )}
        </div>

        {showCharts ? (
          <div className="w-full sm:flex-1 h-20 nodrag">
            <LineChart 
              data={dataPts} 
              height="100%" 
              color={!isAbnormal(marker.interpretation) ? '#3b82f6' : '#ef4444'} 
              referenceRange={{
                min: active.referenceRange.min,
                max: active.referenceRange.max
              }}
              showReferenceLines={showReferenceRanges}
              chartType={chartType}
              showGrid={showGrid}
              showSpikes={showSpikes}
              unit={formatUnit(active.unit)}
              mini={true}
            />
          </div>
        ) : (
          showDate && (
            <div className="w-full sm:w-32 sm:ml-auto flex flex-col items-end shrink-0 sm:pr-4">
               <span className="text-[9px] font-black text-gray-400 uppercase tracking-widest leading-none mb-1">{t('common.date')}</span>
               <span className="text-xs font-bold text-gray-500 font-mono tracking-tight">{new Date(marker.source.date).toLocaleDateString()}</span>
            </div>
          )
        )}

        <button className="hidden sm:block p-2 text-gray-200 hover:text-blue-500 transition-colors" onClick={() => navigate(`/biomarkers/details/${targetId}`)}>
          <ChevronRight className="w-5 h-5" />
        </button>
      </div>
    );
  };

  const renderTable = (groupName: string, markers: BiomarkerObservation[]) => (
    <div key={groupName} className="space-y-4">
      <div className="flex items-center space-x-4">
        <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border opacity-50"></div>
        <h2 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.3em] whitespace-nowrap">
          {groupName}
        </h2>
        <div className="h-px flex-1 bg-gray-100 dark:bg-dark-border opacity-50"></div>
      </div>
      
      <div className="bg-white dark:bg-dark-surface rounded-3xl border border-gray-100 dark:border-dark-border shadow-sm overflow-hidden overflow-x-auto no-scrollbar">
         <table className="min-w-full divide-y divide-gray-50 dark:divide-dark-border">
            <thead className="bg-gray-50/30 dark:bg-dark-bg/30">
              <tr>
                <th 
                  onClick={() => requestSort('displayName')}
                  className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest cursor-pointer hover:text-blue-600 transition-colors"
                >
                  <div className="flex items-center">
                    {t('examination_detail.biomarkers.table.biomarker')}
                    {renderSortIcon('displayName')}
                  </div>
                </th>
                <th 
                  onClick={() => requestSort('value')}
                  className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest cursor-pointer hover:text-blue-600 transition-colors"
                >
                  <div className="flex items-center">
                    {t('examination_detail.biomarkers.table.measured_result')}
                    {renderSortIcon('value')}
                  </div>
                </th>
                <th 
                  onClick={() => requestSort('status')}
                  className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest cursor-pointer hover:text-blue-600 transition-colors"
                >
                  <div className="flex items-center">
                    {t('examination_detail.biomarkers.table.status')}
                    {renderSortIcon('status')}
                  </div>
                </th>
                <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.biomarkers.table.ref_range')}</th>
                {showCharts ? (
                  <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest text-center">{t('examination_detail.biomarkers.table.trend')}</th>
                ) : (
                  showDate && (
                    <th 
                      onClick={() => requestSort('date')}
                      className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest cursor-pointer hover:text-blue-600 transition-colors"
                    >
                      <div className="flex items-center">
                        {t('common.date')}
                        {renderSortIcon('date')}
                      </div>
                    </th>
                  )
                )}
                <th className="px-8 py-4 text-right text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.biomarkers.table.details')}</th>
              </tr>
            </thead>
             <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                 {markers.map((m: BiomarkerObservation) => {
                    const active = getActiveData(m);
                    const mNavigable = !!m.definitionId;
                    return (
                    <tr 
                      key={m.id} 
                      className={`group hover:bg-gray-50/50 dark:hover:bg-dark-bg transition-colors ${mNavigable ? 'cursor-pointer' : ''}`} 
                      onClick={() => mNavigable && navigate(`/biomarkers/details/${m.definitionId}`)}
                    >
                       <td className="px-8 py-5 align-middle max-w-[300px]">
                        <div className="flex items-center gap-2">
                            <span className={`text-sm font-bold text-gray-900 dark:text-dark-text ${mNavigable ? 'group-hover:text-blue-600' : ''} transition-colors whitespace-normal break-words leading-snug min-w-0 flex-1`}>{m.displayName}</span>
                            {m.isTelemetry && (
                              <div className="bg-indigo-50 dark:bg-indigo-900/20 text-indigo-500 rounded p-0.5 shrink-0" title="Telemetry/IoT Data">
                                <Activity className="w-3 h-3" />
                              </div>
                            )}
                            {m.isUnmapped && (
                              <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
                                <UnmappedBiomarkerMenu rawName={m.displayName} onRemapped={onRemapped} />
                              </div>
                            )}
                            {m.info && <Info className="w-3.5 h-3.5 text-blue-400 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" onClick={(e) => { e.stopPropagation(); setSelectedInfo(m); }} />}
                        </div>
                     </td>
                    <td className="px-8 py-5 whitespace-nowrap">
                       <div className="flex items-center space-x-1">
                          <span className={`text-lg font-black ${isAbnormal(m.interpretation) ? 'text-red-600' : 'text-blue-600 dark:text-blue-400'}`}>{formatBiomarkerValue(active.value, precisionProfile)}</span>
                           <span className="text-[10px] font-bold text-gray-400 lowercase">{formatUnit(active.unit)}</span>
                       </div>
                    </td>
                    <td className="px-8 py-5 whitespace-nowrap">
                      <div className="flex items-center">
                        {m.interpretation.toLowerCase().includes('high') || m.interpretation.toLowerCase() === 'h' ? (
                          <ArrowUpCircle className="w-4 h-4 text-red-500 mr-2" />
                        ) : m.interpretation.toLowerCase().includes('low') || m.interpretation.toLowerCase() === 'l' ? (
                          <ArrowDownCircle className="w-4 h-4 text-blue-500 mr-2" />
                        ) : (
                          <CheckCircle2 className="w-4 h-4 text-green-200 dark:text-green-800/30 mr-2" />
                        )}
                        <span className={`text-[10px] font-bold uppercase tracking-wider ${isAbnormal(m.interpretation) ? (m.interpretation.toLowerCase().includes('high') ? 'text-red-600' : 'text-blue-600') : 'text-gray-400'}`}>
                          {m.interpretation}
                        </span>
                      </div>
                    </td>
                    <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-400 font-bold font-mono tracking-tight">{active.referenceRange.displayText}</td>
                    {showCharts ? (
                      <td className="px-8 py-5 whitespace-nowrap text-center">
                         <div className="h-10 w-24 mx-auto">
                            <LineChart 
                              data={m._rawJson?.history?.slice(-10).map((d: any) => ({ 
                                name: new Date(d.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
                                value: activeDataMode === 'raw' ? (d.raw_value || d.value) : d.value 
                              })) || []}
                              height="100%"
                              color={!isAbnormal(m.interpretation) ? '#3b82f6' : '#ef4444'}
                              hideAxes={true}
                              hideTooltip={false}
                              unit={formatUnit(active.unit)}
                            />
                         </div>
                      </td>
                    ) : (
                      showDate && <td className="px-8 py-5 whitespace-nowrap text-xs text-gray-400 font-bold font-mono">{new Date(m.source.date).toLocaleDateString()}</td>
                    )}
                    <td className="px-8 py-5 whitespace-nowrap text-right">
                       <div className="flex items-center justify-end space-x-2">
                          {onDelete && (
                            <button 
                              type="button"
                              onClick={(e) => { 
                                e.preventDefault();
                                e.stopPropagation(); 
                                onDelete(m.id); 
                              }}
                              className="p-1 text-gray-300 transition-colors hover:text-red-500 opacity-0 group-hover:opacity-100"
                              title={t('common.delete')}
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          )}
                          <ChevronRight className="w-5 h-5 text-gray-200 group-hover:text-blue-600 transition-colors" />
                       </div>
                    </td>
                 </tr>
                 );
                 })}
            </tbody>
         </table>
      </div>
    </div>
  );

  return (
    <div className="space-y-8 sm:space-y-12 animate-in fade-in duration-500">
      {/* Internal Control Bar */}
      {!hideDataModeToggle && (
        <div className="flex items-center justify-end">
           <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
              <button 
                 onClick={() => handleDataModeChange('normalized')}
                 className={`px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all ${activeDataMode === 'normalized' ? 'bg-white dark:bg-dark-surface text-indigo-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
              >
                 {t('biomarkers.data_modes.normalized')}
              </button>
              <button 
                 onClick={() => handleDataModeChange('raw')}
                 className={`px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all ${activeDataMode === 'raw' ? 'bg-white dark:bg-dark-surface text-amber-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
              >
                 {t('biomarkers.data_modes.raw')}
              </button>
           </div>
        </div>
      )}

      {viewMode === 'table' ? (
        sortedGroupedData.map(([groupName, markers]) => renderTable(groupName, markers))
      ) : (
        groupedData.map(([groupName, markers]) => (
          <div key={groupName} className="space-y-4 sm:space-y-6">
            <div className="flex items-center space-x-4">
              <div className="h-px flex-1 bg-gray-200 dark:bg-dark-border"></div>
              <h2 className="text-[10px] sm:text-sm font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] whitespace-nowrap">
                {groupName}
              </h2>
              <div className="h-px flex-1 bg-gray-200 dark:bg-dark-border"></div>
            </div>
            <div className={viewMode === 'grid' ? 'grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4 sm:gap-6' : 'space-y-3 sm:space-y-4'}>
              {markers.map(viewMode === 'grid' ? renderGridCard : renderListItem)}
            </div>
          </div>
        ))
      )}

      {/* Biomarker Info Modal */}
      {selectedInfo && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" onClick={() => setSelectedInfo(null)}>
          <div className="bg-white dark:bg-dark-surface w-full max-w-lg rounded-[2.5rem] shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-300" onClick={e => e.stopPropagation()}>
            <div className="p-8 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <div className="p-3 bg-blue-50 dark:bg-blue-900/30 rounded-2xl shadow-inner"><Info className="w-6 h-6 text-blue-600" /></div>
                <div>
                  <h3 className="text-xl font-black text-[#1a2b4b] dark:text-dark-text tracking-tight">{selectedInfo.displayName}</h3>
                  <p className="text-[10px] text-gray-400 font-mono font-black uppercase tracking-widest">{selectedInfo.slug || 'Clinical Parameter'}</p>
                </div>
              </div>
              <button onClick={() => setSelectedInfo(null)} className="p-2 hover:bg-gray-100 rounded-full transition-colors"><X className="w-6 h-6 text-gray-300" /></button>
            </div>
            <div className="p-10 max-h-[60vh] overflow-y-auto no-scrollbar">
              <div className="prose dark:prose-invert max-w-none">
                {selectedInfo.info && (
                  selectedInfo.info.includes('</') || selectedInfo.info.includes('<br') ? (
                    <div 
                      className="text-gray-700 dark:text-dark-text leading-relaxed"
                      dangerouslySetInnerHTML={{ __html: selectedInfo.info }}
                    />
                  ) : (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedInfo.info}</ReactMarkdown>
                  )
                )}
              </div>
              {!selectedInfo.info && (
                <div className="text-center py-10">
                   <AlertCircle className="w-12 h-12 text-gray-200 mx-auto mb-4" />
                   <p className="text-gray-400 font-bold uppercase tracking-widest text-xs">{t('biomarkers.no_clinical_info')}</p>
                </div>
              )}
            </div>
            <div className="p-8 bg-gray-50/50 dark:bg-dark-bg/20 border-t border-gray-100 flex justify-end">
               <button onClick={() => setSelectedInfo(null)} className="px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95 uppercase tracking-widest">{t('common.dismiss')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});
