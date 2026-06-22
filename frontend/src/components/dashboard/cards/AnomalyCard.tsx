import React from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { X, Siren, TrendingUp, TrendingDown, AlertTriangle, ArrowUpRight, ArrowDownRight, Check } from 'lucide-react';
import { usePatientStore } from '../../../store/slices/patientSlice';
import { getAnomalies, BiomarkerAnomaly } from '../../../services/analyticsService';
import { formatBiomarkerValue, formatUnit } from '../../../utils/biomarkerUtils';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';

const severityConfig: Record<string, { icon: React.ReactNode; classes: string }> = {
  critical: { icon: <AlertTriangle className="w-4 h-4" />, classes: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-900/30 text-red-700 dark:text-red-400' },
  warning: { icon: <AlertTriangle className="w-4 h-4" />, classes: 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-900/30 text-amber-700 dark:text-amber-400' },
  info: { icon: <Siren className="w-4 h-4" />, classes: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-900/30 text-blue-700 dark:text-blue-400' },
};

const typeIcon = (type: string) => {
  if (type === 'upward_trend') return <TrendingUp className="w-3.5 h-3.5" />;
  if (type === 'downward_trend') return <TrendingDown className="w-3.5 h-3.5" />;
  if (type === 'above_reference') return <ArrowUpRight className="w-3.5 h-3.5" />;
  if (type === 'below_reference') return <ArrowDownRight className="w-3.5 h-3.5" />;
  return <AlertTriangle className="w-3.5 h-3.5" />;
};

export const AnomalyCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const { currentPatient } = usePatientStore();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children } = props;

  const [anomalies, setAnomalies] = React.useState<BiomarkerAnomaly[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);

  React.useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setIsLoading(true);
      const result = await getAnomalies(currentPatient?.id);
      if (!cancelled) {
        setAnomalies(result);
        setIsLoading(false);
      }
    };
    if (currentPatient?.id) load();
    else { setAnomalies([]); setIsLoading(false); }
    return () => { cancelled = true; };
  }, [currentPatient?.id]);

  const sorted = React.useMemo(() => {
    const order = { critical: 0, warning: 1, info: 2 };
    return [...anomalies].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
  }, [anomalies]);

  const counts = React.useMemo(() => ({
    critical: anomalies.filter(a => a.severity === 'critical').length,
    warning: anomalies.filter(a => a.severity === 'warning').length,
  }), [anomalies]);

  return (
    <div
      ref={ref}
      style={style}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 flex flex-col relative group ${isEditMode ? '' : 'overflow-hidden'}`}
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

      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <div className={`p-2 rounded-xl ${counts.critical > 0 ? 'bg-red-50 dark:bg-red-900/30' : 'bg-amber-50 dark:bg-amber-900/30'}`}>
            <Siren className={`w-5 h-5 ${counts.critical > 0 ? 'text-red-500' : 'text-amber-500'}`} />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight">
            {t('dashboard.cards.anomaly_alerts', { defaultValue: 'Anomaly Detection' })}
          </h3>
        </div>
        {anomalies.length > 0 && (
          <span className={`text-[10px] font-black border px-3 py-1 rounded-lg uppercase tracking-wider ${counts.critical > 0 ? 'bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 border-red-100 dark:border-red-900/30' : 'bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border-amber-100 dark:border-amber-900/30'}`}>
            {anomalies.length} {t('dashboard.status.detected', { defaultValue: 'detected' })}
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar space-y-2 pr-1">
        {isLoading ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            {t('common.loading', { defaultValue: 'Loading...' })}
          </div>
        ) : sorted.length > 0 ? sorted.map((anomaly, i) => {
          const cfg = severityConfig[anomaly.severity] || severityConfig.info;
          const targetId = anomaly.biomarker_id;
          return (
            <div key={i} className={`p-3 rounded-xl border transition-all ${cfg.classes}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-start space-x-2 min-w-0">
                  <span className="flex-shrink-0 mt-0.5">{typeIcon(anomaly.type)}</span>
                  <div className="min-w-0">
                    {anomaly.biomarker && (
                      <p className="font-bold text-sm truncate">
                        {targetId ? (
                          <Link to={`/biomarkers/details/${targetId}`} className="hover:underline">{anomaly.biomarker}</Link>
                        ) : anomaly.biomarker}
                        {anomaly.value != null && (
                          <span className="ml-2 font-black">{formatBiomarkerValue(anomaly.value, precisionProfile)}{anomaly.unit ? ` ${formatUnit(anomaly.unit)}` : ''}</span>
                        )}
                      </p>
                    )}
                    <p className="text-[11px] mt-0.5 opacity-80 leading-tight">{anomaly.message}</p>
                  </div>
                </div>
                <span className="text-[8px] font-black uppercase tracking-tighter opacity-70 flex-shrink-0">{anomaly.severity}</span>
              </div>
            </div>
          );
        }) : (
          <div className="flex flex-col items-center justify-center h-full opacity-40 py-8">
            <Check className="w-12 h-12 text-green-500 mb-2" />
            <p className="text-sm font-bold text-gray-500">{t('dashboard.status.no_anomalies', { defaultValue: 'No anomalies detected' })}</p>
            <p className="text-[10px] text-gray-400 mt-1">{t('dashboard.status.no_anomalies_desc', { defaultValue: 'All biomarkers within expected patterns' })}</p>
          </div>
        )}
      </div>
      {children}
    </div>
  );
});
