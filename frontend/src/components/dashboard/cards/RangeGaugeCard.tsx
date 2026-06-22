import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { X, Gauge, Info } from 'lucide-react';
import { getFinalStatus, formatUnit, formatBiomarkerValue, getStatusColorClass } from '../../../utils/biomarkerUtils';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';
import { BiomarkerObservation } from '../../../types/biomarker';
import { SearchableBiomarkerSelect } from '../shared/SearchableBiomarkerSelect';
import { BiomarkerInfoModal } from '../shared/BiomarkerInfoModal';

const polarToCartesian = (cx: number, cy: number, r: number, angleDeg: number) => {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
};

const arcPath = (cx: number, cy: number, r: number, startAngle: number, endAngle: number) => {
  const start = polarToCartesian(cx, cy, r, startAngle);
  const end = polarToCartesian(cx, cy, r, endAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`;
};

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export const RangeGaugeCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const { id, isEditMode, availableBiomarkers, data, config, onUpdateConfig, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children } = props;
  const [selectedInfo, setSelectedInfo] = React.useState<any>(null);

  const selectedBiomarker = config?.biomarker;
  const showConfig = isEditMode;

  const latestPoint = React.useMemo(() => {
    if (!data || data.length === 0) return null;
    return data[data.length - 1];
  }, [data]);

  const min = latestPoint?.reference_range_min;
  const max = latestPoint?.reference_range_max;
  const value = latestPoint?.value;

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

  const gauge = React.useMemo(() => {
    if (min == null || max == null || max <= min || value == null || !isFinite(value)) return null;
    const range = max - min;
    const margin = range * 0.5;
    const scaleMin = min - margin;
    const scaleMax = max + margin;
    const position = clamp((value - scaleMin) / (scaleMax - scaleMin), 0, 1);

    const cx = 100;
    const cy = 95;
    const r = 78;
    const angle = 180 + position * 180;
    const needleEnd = polarToCartesian(cx, cy, r - 8, angle);
    const hub = { x: cx, y: cy };

    return {
      cx, cy, r,
      lowArc: arcPath(cx, cy, r, 180, 225),
      normalArc: arcPath(cx, cy, r, 225, 315),
      highArc: arcPath(cx, cy, r, 315, 360),
      needle: `M ${hub.x} ${hub.y} L ${needleEnd.x} ${needleEnd.y}`,
      position,
      hasRange: true,
    };
  }, [value, min, max]);

  const displayBiomarkerName = React.useMemo(() => {
    if (!selectedBiomarker) return t('dashboard.config.select_biomarker');
    const found = availableBiomarkers?.find((b: any) => b.slug === selectedBiomarker || b.name === selectedBiomarker || b === selectedBiomarker);
    return typeof found === 'object' ? found.name : (found || selectedBiomarker);
  }, [selectedBiomarker, availableBiomarkers, t]);

  const biomarkerId = latestPoint?.biomarker_id;
  const isClickable = !isEditMode && !!biomarkerId;

  const handleCardClick = () => {
    if (isClickable) navigate(`/biomarkers/details/${biomarkerId}`);
  };

  return (
    <div
      ref={ref}
      style={{ ...style, zIndex: showConfig ? 100 : (style?.zIndex || 1) }}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 flex flex-col relative group ${isEditMode ? '' : 'overflow-hidden'} ${isClickable ? 'cursor-pointer hover:border-blue-300 dark:hover:border-blue-700/50 hover:shadow-md transition-all' : ''}`}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
      onClick={handleCardClick}
    >
      {isEditMode && onRemove && (
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(id); }}
          className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-[60] hover:bg-red-600 active:scale-95"
        >
          <X className="w-3 h-3" />
        </button>
      )}

      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center space-x-2 min-w-0">
          <div className="p-1.5 bg-indigo-50 dark:bg-indigo-900/30 rounded-lg flex-shrink-0">
            <Gauge className="w-4 h-4 text-indigo-500" />
          </div>
          <h3 className="text-sm font-black text-gray-900 dark:text-dark-text tracking-tight leading-tight break-words">{displayBiomarkerName}</h3>
        </div>
        {latestPoint?.info && (
          <button onClick={(e) => { e.stopPropagation(); setSelectedInfo({ info: latestPoint.info, name: displayBiomarkerName }); }} className="p-1 text-blue-400 hover:text-blue-600 transition-colors flex-shrink-0">
            <Info className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {isEditMode && (
        <div className="mb-3 nodrag" onMouseDown={e => e.stopPropagation()}>
          <SearchableBiomarkerSelect
            value={selectedBiomarker || ''}
            options={availableBiomarkers || []}
            onChange={(val: string) => onUpdateConfig(id, { ...config, biomarker: val })}
            placeholder={t('dashboard.config.select_biomarker')}
            discreet
          />
        </div>
      )}

      {latestPoint ? (
        <div className="flex-1 flex flex-col items-center justify-center">
          {gauge ? (
            <svg viewBox="0 0 200 110" className="w-full max-w-[200px]" >
              <path d={gauge.lowArc} fill="none" stroke="currentColor" className="text-blue-200 dark:text-blue-900/40" strokeWidth="12" strokeLinecap="round" />
              <path d={gauge.normalArc} fill="none" stroke="currentColor" className="text-green-200 dark:text-green-900/40" strokeWidth="12" strokeLinecap="round" />
              <path d={gauge.highArc} fill="none" stroke="currentColor" className="text-red-200 dark:text-red-900/40" strokeWidth="12" strokeLinecap="round" />
              <path d={gauge.needle} fill="none" stroke={interpretation === 'High' ? '#ef4444' : interpretation === 'Low' ? '#3b82f6' : '#10b981'} strokeWidth="3" strokeLinecap="round" />
              <circle cx={gauge.cx} cy={gauge.cy} r="4" fill="#1f2937" className="dark:text-white" />
            </svg>
          ) : (
            <div className="flex items-center justify-center h-[110px] text-gray-400 text-xs">
              {t('biomarkers.no_reference_range', { defaultValue: 'No reference range' })}
            </div>
          )}

          <div className="flex items-baseline space-x-1 -mt-2">
            <span className={`text-3xl font-black tracking-tight ${interpretation === 'High' ? 'text-red-600' : interpretation === 'Low' ? 'text-blue-600' : 'text-gray-900 dark:text-dark-text'}`}>
              {formatBiomarkerValue(value, precisionProfile)}
            </span>
            <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase">{formatUnit(latestPoint.unit)}</span>
          </div>

          <div className="flex items-center justify-between w-full mt-1 text-[9px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">
            <span>{min != null ? formatBiomarkerValue(min, precisionProfile) : '--'}</span>
            <span className="text-green-500">{t('biomarkers.normal', { defaultValue: 'Normal' })}</span>
            <span>{max != null ? formatBiomarkerValue(max, precisionProfile) : '--'}</span>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
          {selectedBiomarker ? t('common.loading', { defaultValue: 'Loading...' }) : t('dashboard.config.select_biomarker')}
        </div>
      )}

      {selectedInfo && (
        <BiomarkerInfoModal info={selectedInfo.info} name={selectedInfo.name} onClose={() => setSelectedInfo(null)} />
      )}
      {children}
    </div>
  );
});
