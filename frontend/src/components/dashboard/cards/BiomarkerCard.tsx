import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  Activity, 
  TrendingUp,
  ChevronDown,
  Info,
  Box
} from 'lucide-react';
import LineChart from '../../charts/LineChart';
import { getFinalStatus, isAbnormal, formatUnit, formatBiomarkerValue } from '../../../utils/biomarkerUtils';
import { BiomarkerObservation, DataSourceType } from '../../../types/biomarker';
import { CardWrapper } from '../shared/CardWrapper';
import { SearchableBiomarkerSelect } from '../shared/SearchableBiomarkerSelect';
import { BiomarkerInfoModal } from '../shared/BiomarkerInfoModal';
import { IconMap } from '../shared/icons';
import { BiomarkerStatusIndicator } from '../shared/BiomarkerStatusIndicator';
import { ReferenceRangeDisplay } from '../shared/ReferenceRangeDisplay';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';
import { useBiomarkerChange } from '../../../hooks/useBiomarkerChange';

import { format, parseISO } from 'date-fns';

export const BiomarkerCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { config, isEditMode, availableBiomarkers, data, onUpdateConfig, children } = props;
  const [selectedInfo, setSelectedInfo] = React.useState<any>(null);
  const [showIconPicker, setShowIconPicker] = React.useState(false);
  const precisionProfile = useBiomarkerPrecisionProfile();
  
  const Icon = IconMap[config.icon] || Activity;
  
  // getBiomarkerTrends returns points sorted ASC, so latest is the last item
  const latestPoint = React.useMemo(() => {
    if (!data || data.length === 0) return null;
    return data[data.length - 1];
  }, [data]);

  const latestValue = latestPoint?.value ?? '--';
  const unit = latestPoint?.unit ?? '';
  
  // Calculate trend change
  const changeInfo = useBiomarkerChange(data);

  // Calculate status using the central utility and reference ranges from the data point
  const status = React.useMemo(() => {
    if (!latestPoint) return 'Normal';
    
    // Construct a partial BiomarkerObservation for the utility
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

  const info = latestPoint?.info;
  const targetId = latestPoint?.biomarker_id;

  const showSparkline = config.showSparkline !== false && data && data.length > 1;

  const displayBiomarkerName = React.useMemo(() => {
    if (!config.biomarker) return t('dashboard.config.select_biomarker');
    const found = availableBiomarkers?.find((b: any) => b.slug === config.biomarker || b.name === config.biomarker || b === config.biomarker);
    return typeof found === 'object' ? found.name : (found || config.biomarker);
  }, [config.biomarker, availableBiomarkers, t]);
  
  const getIconColorClass = (status: string) => {
    const s = status.toLowerCase().trim();
    if (s.includes('high') || s === 'h' || s === 'abnormal') return 'text-red-500';
    if (s.includes('low') || s === 'l') return 'text-blue-500';
    if (s.includes('warning')) return 'text-yellow-500';
    return 'text-green-500';
  };

  return (
    <CardWrapper 
      {...props} 
      ref={ref} 
      className={`${props.className || ''} ${!isEditMode && targetId ? 'cursor-pointer hover:border-blue-200 dark:hover:border-blue-900 transition-all' : ''} relative`}
      onClick={() => !isEditMode && targetId && navigate(`/biomarkers/details/${targetId}`)}
    >
      {isEditMode && (
        <div className="absolute top-2 right-2 flex items-center space-x-1 z-[70]">
          <button 
            onClick={(e) => { e.stopPropagation(); onUpdateConfig(props.id, { ...config, showSparkline: !config.showSparkline }); }}
            className={`p-1.5 rounded-lg transition-all ${config.showSparkline !== false ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-400'}`}
            title="Toggle Sparkline"
          >
            <TrendingUp className="w-3 h-3" />
          </button>
        </div>
      )}

      <div className="flex justify-between items-start mb-6">
        <div className="flex items-center space-x-3 min-w-0">
          <div className="relative flex-shrink-0">
            <button 
              type="button"
              disabled={!isEditMode}
              onClick={(e) => { e.stopPropagation(); setShowIconPicker(!showIconPicker); }}
              className={`w-12 h-12 rounded-2xl flex items-center justify-center transition-all ${isEditMode ? 'bg-gray-100 dark:bg-dark-bg border border-gray-200 dark:border-dark-border hover:border-blue-500' : 'bg-blue-50 dark:bg-blue-900/20'}`}
            >
              <Icon className={`w-6 h-6 ${isEditMode ? 'text-gray-600 dark:text-dark-text' : getIconColorClass(status)}`} />
              {isEditMode && <ChevronDown className="w-3 h-3 ml-1 text-gray-400" />}
            </button>

            {isEditMode && showIconPicker && (
              <div className="absolute top-full left-0 mt-2 z-[150] bg-white/95 dark:bg-dark-surface/95 backdrop-blur-md border border-gray-200 dark:border-dark-border rounded-xl shadow-2xl p-4 w-44 animate-in fade-in slide-in-from-top-1 duration-200" onClick={e => e.stopPropagation()}>
                <p className="text-[9px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-3 px-1 border-b border-gray-100 dark:border-dark-border pb-2">{t('dashboard.config.appearance')}</p>
                <div className="grid grid-cols-4 gap-2">
                  {Object.keys(IconMap).map((iconName) => {
                    const PickerIcon = IconMap[iconName];
                    return (
                      <button
                        key={iconName}
                        type="button"
                        onClick={() => {
                          onUpdateConfig(props.id, { ...config, icon: iconName });
                          setShowIconPicker(false);
                        }}
                        className={`p-2 rounded-lg transition-all border flex items-center justify-center ${config.icon === iconName ? 'bg-blue-600 border-blue-600 text-white shadow-lg scale-110' : 'bg-gray-50 dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-400 hover:bg-blue-50 dark:hover:bg-blue-900/40 hover:text-blue-600 hover:border-blue-200'}`}
                        title={iconName}
                      >
                        <PickerIcon className="w-4 h-4" />
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
          <div className="min-w-0">
            {isEditMode ? (
              <SearchableBiomarkerSelect 
                className="min-w-[140px]"
                value={config.biomarker}
                options={availableBiomarkers}
                onChange={(val) => onUpdateConfig(props.id, { ...config, biomarker: val })}
              />
            ) : (
              <>
                <div className="flex items-center space-x-1">
                  <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">{displayBiomarkerName}</h3>
                  {info && (
                    <button 
                      type="button"
                      onClick={(e) => { 
                        e.preventDefault();
                        e.stopPropagation(); 
                        setSelectedInfo({ info, name: displayBiomarkerName }); 
                      }}
                      className="p-1.5 text-blue-400 hover:text-blue-600 transition-colors"
                    >
                      <Info className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
                <div className="flex flex-col space-y-1 mt-0.5">
                  <ReferenceRangeDisplay displayText={latestPoint?.reference_range_text} compact={true} />
                  {latestPoint?.source_type && (
                    <div className="flex items-center">
                      {latestPoint.source_type === DataSourceType.TELEMETRY ? (
                        <span className="flex items-center space-x-1 px-1.5 py-0.5 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded text-[8px] font-black uppercase tracking-widest border border-indigo-100 dark:border-indigo-900/30">
                          <Activity className="w-2.5 h-2.5" />
                          <span>Telemetry</span>
                        </span>
                      ) : (
                        <span className="flex items-center space-x-1 px-1.5 py-0.5 bg-slate-50 dark:bg-slate-900/20 text-slate-500 dark:text-slate-400 rounded text-[8px] font-black uppercase tracking-widest border border-slate-200 dark:border-slate-800">
                          <Box className="w-2.5 h-2.5" />
                          <span>FHIR</span>
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>

        {!isEditMode && (
          <div className="flex flex-col items-end">
            <BiomarkerStatusIndicator interpretation={status} compact={true} />
            {changeInfo && (
              <span className={`text-[10px] font-black mt-1.5 flex items-center ${changeInfo.color}`}>
                {!changeInfo.isNeutral && (changeInfo.isUp ? '↑' : '↓')}
                {changeInfo.percent}%
              </span>
            )}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-4 mt-auto mb-6">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline space-x-1.5">
            <span className={`text-4xl font-black tracking-tighter ${status.toLowerCase().includes('high') ? 'text-red-600' : (status.toLowerCase().includes('low') ? 'text-blue-600' : 'text-gray-900 dark:text-dark-text')}`}>
              {formatBiomarkerValue(latestValue, precisionProfile)}
            </span>
            <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">
              {formatUnit(unit)}
            </span>
          </div>
        </div>

        {showSparkline && (
          <div className="w-32 h-16 flex-shrink-0 nodrag opacity-80 hover:opacity-100 transition-opacity">
            <LineChart 
              data={data.slice(-10).map((d: any) => ({ 
                name: d.date ? format(parseISO(d.date), 'MMM d') : '', 
                value: d.value 
              }))}
              height="100%"
              color={!isAbnormal(status) ? '#3b82f6' : (status.toLowerCase().includes('high') ? '#ef4444' : '#3b82f6')}
              showGrid={false}
              showReferenceLines={false}
              strokeWidth={3}
              hideAxes={true}
              hideTooltip={false}
              unit={formatUnit(unit)}
            />
          </div>
        )}
      </div>
      
      {selectedInfo && (
        <BiomarkerInfoModal 
          info={selectedInfo.info} 
          name={selectedInfo.name} 
          onClose={() => setSelectedInfo(null)} 
        />
      )}
      {children}
    </CardWrapper>
  );
});
