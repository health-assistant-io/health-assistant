import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  X, 
  TrendingUp,
  Info,
  Settings,
  Eye,
  EyeOff,
  Search,
  ZoomIn,
  Activity
} from 'lucide-react';
import LineChart from '../../charts/LineChart';
import { getFinalStatus, isAbnormal, formatUnit, formatBiomarkerValue } from '../../../utils/biomarkerUtils';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';
import { useBiomarkerChange } from '../../../hooks/useBiomarkerChange';
import { BiomarkerObservation } from '../../../types/biomarker';
import { SearchableBiomarkerSelect } from '../shared/SearchableBiomarkerSelect';
import { BiomarkerInfoModal } from '../shared/BiomarkerInfoModal';
import { BiomarkerStatusIndicator } from '../shared/BiomarkerStatusIndicator';
import { ReferenceRangeDisplay } from '../shared/ReferenceRangeDisplay';

export const TrendsCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const { id, isEditMode, selectedBiomarker, setSelectedBiomarker, trendsData, mockTrends, availableBiomarkers, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children, config, onUpdateConfig 
  } = props;
  const [selectedInfo, setSelectedInfo] = React.useState<any>(null);
  const [showConfig, setShowConfig] = React.useState(false);
  
  const dateRange = config?.dateRange || 'all-time';
  const chartType = config?.chartType || 'line';
  const showGrid = config?.showGrid !== false;
  const showReferenceLines = config?.showReferenceLines !== false;
  const showZoom = config?.showZoom || false;
  const interactiveZoom = config?.interactiveZoom || false;
  const showLatestValue = config?.showLatestValue !== false;
  const showPercentageChange = config?.showPercentageChange !== false;

  // getBiomarkerTrends returns points sorted ASC, so latest is the last item
  const latestPoint = React.useMemo(() => {
    if (!trendsData || trendsData.length === 0) return null;
    return trendsData[trendsData.length - 1];
  }, [trendsData]);

  const info = latestPoint?.info;
  const biomarkerId = latestPoint?.biomarker_id;

  const filteredTrendsData = React.useMemo(() => {
    if (!trendsData || trendsData.length === 0) return [];
    
    if (dateRange === 'all-time') return trendsData;

    const now = new Date();
    let cutoff = new Date();
    if (dateRange === '30d') cutoff.setDate(now.getDate() - 30);
    else if (dateRange === '90d') cutoff.setDate(now.getDate() - 90);
    else if (dateRange === '1y') cutoff.setFullYear(now.getFullYear() - 1);

    return trendsData.filter((d: any) => new Date(d.date) >= cutoff);
  }, [trendsData, dateRange]);

  const interpretation = React.useMemo(() => {
    if (!latestPoint) return 'Normal';
    const mockObs: any = {
      value: { raw: latestPoint.value },
      interpretation: latestPoint.status || 'Normal',
      referenceRange: {
        min: latestPoint.reference_range_min,
        max: latestPoint.reference_range_max,
        displayText: latestPoint.reference_range_text || '--'
      }
    };
    return getFinalStatus(mockObs as BiomarkerObservation);
  }, [latestPoint]);

  const changeInfo = useBiomarkerChange(trendsData);

  const displayBiomarkerName = React.useMemo(() => {
    if (!selectedBiomarker) return t('dashboard.config.select_biomarker');
    const found = availableBiomarkers?.find((b: any) => b.slug === selectedBiomarker || b.name === selectedBiomarker || b === selectedBiomarker);
    return typeof found === 'object' ? found.name : (found || selectedBiomarker);
  }, [selectedBiomarker, availableBiomarkers, t]);

  return (
    <div 
      ref={ref}
      style={{ ...style, zIndex: showConfig ? 100 : (style?.zIndex || 1) }}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 flex flex-col relative group ${isEditMode ? '' : 'overflow-hidden'}`}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
    >
      {isEditMode && (
        <div className="absolute -top-3 -right-3 flex items-center space-x-2 z-[70] opacity-0 group-hover:opacity-100 transition-all">
          <button 
            onClick={(e) => { e.stopPropagation(); setShowConfig(!showConfig); }}
            className={`bg-white dark:bg-dark-surface rounded-full p-2 shadow-xl border-4 border-white dark:border-dark-surface transition-all hover:scale-110 active:scale-95 ${showConfig ? 'text-blue-600' : 'text-gray-400'}`}
            title={t('common.settings')}
          >
            <Settings className="w-4 h-4" />
          </button>
          {onRemove && (
            <button 
              onClick={(e) => { e.stopPropagation(); onRemove(id); }}
              className="bg-red-500 text-white rounded-full p-2 shadow-xl hover:bg-red-600 active:scale-95 border-4 border-white dark:border-dark-surface"
              title={t('common.delete')}
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      )}
      
      <div className="flex justify-between items-start mb-4">
        <div 
          className={`flex items-center space-x-2 ${!isEditMode && biomarkerId ? 'cursor-pointer group/title' : ''}`}
          onClick={() => !isEditMode && biomarkerId && navigate(`/biomarkers/details/${biomarkerId}`)}
        >
          <TrendingUp className="w-5 h-5 text-blue-500 group-hover/title:scale-110 transition-transform" />
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text group-hover/title:text-blue-600 transition-colors">
                {selectedBiomarker ? t('dashboard.cards.trend_graph_with_name', { name: displayBiomarkerName }) : t('dashboard.cards.trend_graph')}
              </h3>
            </div>
            <div className="flex items-center space-x-2">
              <p className="text-xs text-gray-400 dark:text-dark-muted font-medium uppercase tracking-wider">
                {latestPoint ? formatUnit(latestPoint.unit) : 'Longitudinal tracking'}
              </p>
              {showReferenceLines && latestPoint?.reference_range_text && (
                 <ReferenceRangeDisplay displayText={latestPoint.reference_range_text} compact={true} />
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          {isEditMode ? (
            <SearchableBiomarkerSelect 
              className="min-w-[160px]"
              value={selectedBiomarker || (availableBiomarkers && availableBiomarkers[0]?.slug) || ''}
              options={availableBiomarkers}
              onChange={(val) => setSelectedBiomarker(val)}
            />
          ) : (
            <div className="flex flex-col items-end shrink-0">
              <div className="flex items-center space-x-2">
                {info && (
                  <button 
                    type="button"
                    onClick={(e) => { 
                      e.preventDefault();
                      e.stopPropagation(); 
                      setSelectedInfo({ info, name: displayBiomarkerName }); 
                    }}
                    className="p-1 text-blue-400 hover:text-blue-600 transition-colors relative z-30"
                  >
                    <Info className="w-3.5 h-3.5" />
                  </button>
                )}
                <BiomarkerStatusIndicator interpretation={interpretation} compact={true} />
              </div>
              {showPercentageChange && changeInfo && (
                <span className={`text-[10px] font-black mt-1.5 ${changeInfo.color}`}>
                  {!changeInfo.isNeutral && (changeInfo.isUp ? '↑' : '↓')}
                  {changeInfo.percent}%
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {(showLatestValue || isEditMode) && latestPoint && (
        <div className="mb-4">
          <div className="flex items-baseline space-x-1">
            <span className={`text-3xl font-black tracking-tight ${interpretation.toLowerCase().includes('high') ? 'text-red-600' : (interpretation.toLowerCase().includes('low') ? 'text-blue-600' : 'text-gray-900 dark:text-dark-text')}`}>
              {formatBiomarkerValue(latestPoint.value, precisionProfile)}
            </span>
            <span className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase">{formatUnit(latestPoint.unit)}</span>
          </div>
        </div>
      )}

      {isEditMode && showConfig && (
        <div className="mb-6 p-6 bg-gray-50 dark:bg-dark-bg rounded-[2rem] border border-gray-100 dark:border-dark-border space-y-6 animate-in slide-in-from-top-4 duration-300 nodrag relative shadow-inner" onMouseDown={e => e.stopPropagation()}>
          <div className="grid grid-cols-2 gap-6">
            <div className="space-y-4">
              <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('dashboard.config.chart_options')}</p>
              <div className="flex flex-wrap gap-2">
                <button 
                  onClick={() => onUpdateConfig(id, { ...config, chartType: 'line' })}
                  className={`px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all ${chartType === 'line' ? 'bg-blue-600 text-white border-blue-600 shadow-md' : 'bg-white dark:bg-dark-surface text-gray-600 border-gray-100'}`}
                >
                  Line
                </button>
                <button 
                  onClick={() => onUpdateConfig(id, { ...config, chartType: 'area' })}
                  className={`px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all ${chartType === 'area' ? 'bg-blue-600 text-white border-blue-600 shadow-md' : 'bg-white dark:bg-dark-surface text-gray-600 border-gray-100'}`}
                >
                  Area
                </button>
                <button 
                  onClick={() => onUpdateConfig(id, { ...config, chartType: 'bar' })}
                  className={`px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all ${chartType === 'bar' ? 'bg-blue-600 text-white border-blue-600 shadow-md' : 'bg-white dark:bg-dark-surface text-gray-600 border-gray-100'}`}
                >
                  Bar
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                <button 
                  onClick={() => onUpdateConfig(id, { ...config, showGrid: !showGrid })}
                  className={`flex items-center space-x-2 px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all ${showGrid ? 'bg-indigo-600 text-white border-indigo-600 shadow-md' : 'bg-white dark:bg-dark-surface text-gray-600 border-gray-100'}`}
                >
                  {showGrid ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                  <span>{t('dashboard.config.grid')}</span>
                </button>
                <button 
                  onClick={() => onUpdateConfig(id, { ...config, showReferenceLines: !showReferenceLines })}
                  className={`flex items-center space-x-2 px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all ${showReferenceLines ? 'bg-indigo-600 text-white border-indigo-600 shadow-md' : 'bg-white dark:bg-dark-surface text-gray-600 border-gray-100'}`}
                >
                  {showReferenceLines ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                  <span>{t('dashboard.config.ref_lines')}</span>
                </button>
                <button 
                  onClick={() => onUpdateConfig(id, { ...config, showZoom: !showZoom })}
                  className={`flex items-center space-x-2 px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all ${showZoom ? 'bg-emerald-600 text-white border-emerald-600 shadow-md' : 'bg-white dark:bg-dark-surface text-gray-600 border-gray-100'}`}
                >
                  <Search className="w-3.5 h-3.5" />
                  <span>{t('dashboard.config.brush')}</span>
                </button>
                <button 
                  onClick={() => onUpdateConfig(id, { ...config, interactiveZoom: !interactiveZoom })}
                  className={`flex items-center space-x-2 px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all ${interactiveZoom ? 'bg-amber-500 text-white border-amber-500 shadow-md' : 'bg-white dark:bg-dark-surface text-gray-600 border-gray-100'}`}
                >
                  <ZoomIn className="w-3.5 h-3.5" />
                  <span>{t('dashboard.config.zoom')}</span>
                </button>
              </div>
            </div>

            <div className="space-y-4">
              <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('dashboard.config.date_range')}</p>
              <div className="flex bg-white dark:bg-dark-surface p-1 rounded-xl border border-gray-100 dark:border-dark-border">
                {[
                  { id: '30d', label: '30D' },
                  { id: '90d', label: '3M' },
                  { id: '1y', label: '1Y' },
                  { id: 'all-time', label: 'ALL' }
                ].map(range => (
                  <button 
                    key={range.id}
                    onClick={() => onUpdateConfig(id, { ...config, dateRange: range.id })}
                    className={`flex-1 py-2 text-[10px] font-black rounded-lg transition-all ${dateRange === range.id ? 'bg-blue-600 text-white shadow-md' : 'text-gray-400 hover:text-gray-600'}`}
                  >
                    {range.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
          
          <button 
            onClick={() => setShowConfig(false)}
            className="w-full py-3 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 font-black text-xs uppercase tracking-widest rounded-2xl hover:bg-blue-100 transition-all border border-blue-100 dark:border-blue-900/30"
          >
            {t('dashboard.config.apply_settings')}
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0">
        <LineChart 
          data={filteredTrendsData && filteredTrendsData.length > 0 ? filteredTrendsData.map((d:any) => ({name: new Date(d.date).toLocaleDateString(), value: d.value})) : mockTrends}
          dataKey="value"
          xAxisKey="name"
          color={!isAbnormal(interpretation) ? '#3b82f6' : '#ef4444'}
          referenceRange={{
            min: latestPoint?.reference_range_min,
            max: latestPoint?.reference_range_max
          }}
          showReferenceLines={showReferenceLines && !!latestPoint}
          chartType={chartType}
          showGrid={showGrid}
          showBrush={showZoom}
          interactiveZoom={interactiveZoom}
        />
      </div>

      {selectedInfo && (
        <BiomarkerInfoModal 
          info={selectedInfo.info} 
          name={selectedInfo.name} 
          onClose={() => setSelectedInfo(null)} 
        />
      )}
      {children}
    </div>
  );
});
