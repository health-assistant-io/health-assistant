import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { X, HeartPulse, TrendingUp, TrendingDown, Minus, CalendarClock, ChevronDown, AlertTriangle } from 'lucide-react';
import { getFinalStatus, formatBiomarkerValue, formatUnit, getStatusColorClass } from '../../../utils/biomarkerUtils';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';
import { BiomarkerObservation } from '../../../types/biomarker';

interface AbnormalItem {
  name: string;
  slug: string;
  biomarker_id?: string;
  value: number;
  unit: string;
  status: string;
}

export const HealthSummaryCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children, trendsData, data } = props;
  const [showAbnormal, setShowAbnormal] = React.useState(false);

  React.useEffect(() => {
    if (!showAbnormal) return;
    const handleClickOutside = (event: MouseEvent) => {
      const card = document.querySelector(`[data-card-id="${id}"]`);
      if (card && !card.contains(event.target as Node)) {
        setShowAbnormal(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showAbnormal, id]);

  const { abnormalCount, totalTracked, abnormalItems, upCount, downCount } = React.useMemo(() => {
    let abn = 0;
    let total = 0;
    const items: AbnormalItem[] = [];
    let up = 0;
    let down = 0;

    if (trendsData && typeof trendsData === 'object') {
      for (const slug of Object.keys(trendsData)) {
        const points: any[] = trendsData[slug];
        if (!points || points.length === 0) continue;
        total++;
        const latest = points[points.length - 1];

        const mockObs: any = {
          value: { raw: latest.value },
          interpretation: latest.status || 'Normal',
          referenceRange: {
            min: latest.reference_range_min,
            max: latest.reference_range_max,
          },
        };
        const status = getFinalStatus(mockObs as BiomarkerObservation);
        if (status === 'High' || status === 'Low') {
          abn++;
          items.push({
            name: latest.name || slug,
            slug,
            biomarker_id: latest.biomarker_id,
            value: latest.value,
            unit: latest.unit || '',
            status,
          });
        }

        if (points.length >= 2) {
          const diff = latest.value - points[points.length - 2].value;
          if (diff > 0) up++;
          else if (diff < 0) down++;
        }
      }
    }

    items.sort((a, b) => (a.status === 'High' ? -1 : 1));

    return { abnormalCount: abn, totalTracked: total, abnormalItems: items, upCount: up, downCount: down };
  }, [trendsData]);

  const examDate = data?.examination_date;
  const examDays = examDate ? Math.floor((Date.now() - new Date(examDate).getTime()) / 86400000) : null;

  let trendLabel: string;
  let trendIcon: React.ReactNode;
  let trendTone: string;
  if (upCount > downCount) {
    trendLabel = t('dashboard.summary.trend_rising', { defaultValue: '{{count}} rising', count: upCount });
    trendIcon = <TrendingUp className="w-4 h-4" />;
    trendTone = 'blue';
  } else if (downCount > upCount) {
    trendLabel = t('dashboard.summary.trend_falling', { defaultValue: '{{count}} falling', count: downCount });
    trendIcon = <TrendingDown className="w-4 h-4" />;
    trendTone = 'blue';
  } else {
    trendLabel = t('dashboard.summary.stable', { defaultValue: 'Stable' });
    trendIcon = <Minus className="w-4 h-4" />;
    trendTone = 'gray';
  }

  const toneClasses: Record<string, string> = {
    red: 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20',
    green: 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/20',
    blue: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20',
    gray: 'text-gray-600 dark:text-dark-muted bg-gray-50 dark:bg-dark-bg',
  };

  const handleAbnormalClick = (item: AbnormalItem) => {
    if (item.biomarker_id) {
      navigate(`/biomarkers/details/${item.biomarker_id}`);
    }
  };

  return (
    <div
      ref={ref}
      data-card-id={id}
      style={style}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-5 flex flex-col relative group ${isEditMode ? '' : (showAbnormal ? 'overflow-visible' : 'overflow-hidden')}`}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
    >
      {isEditMode && onRemove && (
        <button
          onClick={(e) => { e.stopPropagation(); onRemove(id); }}
          className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-[60] hover:bg-red-600 active:scale-95"
        >
          <X className="w-3 h-3" />
        </button>
      )}

      <div className="flex items-center space-x-2 mb-3">
        <h3 className="text-xs font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest">
          {t('dashboard.cards.health_summary', { defaultValue: 'Health Summary' })}
        </h3>
      </div>

      <div className="flex-1 grid grid-cols-3 gap-3 nodrag">
        {/* Abnormal stat — clickable to expand list */}
        <button
          type="button"
          onClick={() => abnormalCount > 0 && setShowAbnormal(!showAbnormal)}
          className={`text-left rounded-xl p-3 flex flex-col justify-between transition-all ${toneClasses[abnormalCount > 0 ? 'red' : 'green']} ${abnormalCount > 0 ? 'cursor-pointer hover:scale-[1.02]' : 'cursor-default'}`}
        >
          <div className="flex items-center space-x-1.5 opacity-80">
            <HeartPulse className="w-4 h-4" />
            <span className="text-[9px] font-black uppercase tracking-tight">{t('dashboard.summary.abnormal', { defaultValue: 'Abnormal' })}</span>
            {abnormalCount > 0 && (
              <ChevronDown className={`w-3 h-3 transition-transform ${showAbnormal ? 'rotate-180' : ''}`} />
            )}
          </div>
          <div className="mt-1">
            <span className="text-xl font-black tracking-tight">{abnormalCount}</span>
            <span className="ml-1 text-[9px] font-bold opacity-60">
              {t('dashboard.summary.of_tracked', { defaultValue: 'of {{count}}', count: totalTracked })}
            </span>
          </div>
        </button>

        {/* Trend direction */}
        <div className={`rounded-xl p-3 flex flex-col justify-between ${toneClasses[trendTone]}`}>
          <div className="flex items-center space-x-1.5 opacity-80">
            {trendIcon}
            <span className="text-[9px] font-black uppercase tracking-tight">{t('dashboard.summary.trend', { defaultValue: 'Trend' })}</span>
          </div>
          <div className="mt-1">
            <span className="text-sm font-black tracking-tight leading-tight block">{trendLabel}</span>
            {totalTracked > 0 && (
              <span className="text-[9px] font-bold opacity-60">
                {t('dashboard.summary.of_biomarkers', { defaultValue: 'of {{count}} biomarkers', count: totalTracked })}
              </span>
            )}
          </div>
        </div>

        {/* Last exam */}
        {examDays !== null && (
          <div className={`rounded-xl p-3 flex flex-col justify-between ${toneClasses['gray']}`}>
            <div className="flex items-center space-x-1.5 opacity-80">
              <CalendarClock className="w-4 h-4" />
              <span className="text-[9px] font-black uppercase tracking-tight">{t('dashboard.summary.last_exam', { defaultValue: 'Last Exam' })}</span>
            </div>
            <div className="mt-1">
              <span className="text-xl font-black tracking-tight">
                {examDays === 0 ? t('dashboard.summary.today', { defaultValue: 'Today' }) : `${examDays}d`}
              </span>
              {examDays > 0 && <span className="ml-1 text-[9px] font-bold opacity-60">{t('dashboard.summary.ago', { defaultValue: 'ago' })}</span>}
            </div>
          </div>
        )}
      </div>

      {/* Floating abnormal biomarker dropdown */}
      {showAbnormal && abnormalItems.length > 0 && (
        <div className="absolute left-4 right-4 top-full z-[80] bg-white dark:bg-dark-surface rounded-xl border border-gray-100 dark:border-dark-border shadow-2xl p-2 space-y-1 max-h-[280px] overflow-y-auto custom-scrollbar animate-in fade-in slide-in-from-top-2 duration-200 nodrag">
            <p className="text-[9px] font-black uppercase tracking-widest text-gray-400 px-2 pb-1">
              {t('dashboard.summary.abnormal', { defaultValue: 'Abnormal' })} ({abnormalItems.length})
            </p>
            {abnormalItems.map((item, i) => (
              <button
                key={i}
                type="button"
                onClick={() => handleAbnormalClick(item)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg border transition-all hover:scale-[1.01] text-left ${getStatusColorClass(item.status)}`}
              >
                <div className="flex items-center space-x-2 min-w-0">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
                  <span className="text-xs font-bold truncate">{item.name}</span>
                </div>
                <div className="flex items-center space-x-2 flex-shrink-0">
                  <span className="text-xs font-black">{formatBiomarkerValue(item.value, precisionProfile)} {formatUnit(item.unit)}</span>
                  <span className="text-[9px] font-black uppercase">{item.status}</span>
                </div>
              </button>
            ))}
        </div>
      )}

      {children}
    </div>
  );
});
