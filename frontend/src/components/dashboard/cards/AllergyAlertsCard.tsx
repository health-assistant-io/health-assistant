import React from 'react';
import { useTranslation } from 'react-i18next';
import { 
  X, 
  ShieldAlert,
  AlertTriangle,
  Check
} from 'lucide-react';
import { AllergyIntolerance } from '../../../services/allergyService';

export const AllergyAlertsCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children, data } = props;
  
  const activeAllergies: AllergyIntolerance[] = (data || []).filter(
    (a: AllergyIntolerance) => a.clinical_status?.toLowerCase() === 'active'
  );

  const getCriticalityStyles = (criticality?: string) => {
    switch (criticality) {
      case 'high':
        return 'bg-red-50/50 border-red-200 text-red-700 dark:bg-red-900/10 dark:border-red-900/30 dark:text-red-400';
      case 'low':
        return 'bg-blue-50/50 border-blue-200 text-blue-700 dark:bg-blue-900/10 dark:border-blue-900/30 dark:text-blue-400';
      default:
        return 'bg-gray-50/50 border-gray-200 text-gray-700 dark:bg-dark-bg/50 dark:border-dark-border dark:text-dark-text';
    }
  };

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

      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center space-x-2">
          <div className="p-2 bg-red-50 dark:bg-red-900/30 rounded-xl">
            <ShieldAlert className="w-5 h-5 text-red-500" />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight">{t('dashboard.cards.clinical_alerts')}</h3>
        </div>
        <span className="bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 text-[10px] font-black border border-red-100 dark:border-red-900/30 px-3 py-1 rounded-lg uppercase tracking-wider transition-all animate-pulse">
          {activeAllergies.length} {t('dashboard.status.active')}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar space-y-3 pr-1">
        {activeAllergies.length > 0 ? activeAllergies.map((allergy) => (
          <div 
            key={allergy.id}
            className={`p-3 rounded-xl border transition-all ${getCriticalityStyles(allergy.criticality)} dark:bg-dark-bg dark:border-dark-border dark:text-dark-text`}
          >
            <div className="flex justify-between items-start">
              <div className="flex items-center space-x-2">
                <AlertTriangle className={`w-4 h-4 ${allergy.criticality === 'high' ? 'text-red-500' : 'text-blue-500'}`} />
                <span className="font-bold text-sm">{allergy.code.text}</span>
              </div>
              {allergy.criticality && (
                <span className="text-[9px] font-black uppercase tracking-tighter opacity-70">
                  {allergy.criticality} risk
                </span>
              )}
            </div>
            {allergy.reactions && allergy.reactions.length > 0 && (
              <p className="text-[11px] mt-1.5 opacity-80 leading-tight">
                <span className="font-semibold">Reactions:</span> {allergy.reactions.map(r => r.manifestation).join(', ')}
              </p>
            )}
            {allergy.note && (
              <p className="text-[10px] mt-1 italic opacity-60 truncate">"{allergy.note}"</p>
            )}
          </div>
        )) : (
          <div className="flex flex-col items-center justify-center h-full opacity-40 py-8">
            <Check className="w-12 h-12 text-green-500 mb-2" />
            <p className="text-sm font-bold text-gray-500">{t('dashboard.status.no_allergies')}</p>
          </div>
        )}
      </div>
      {children}
    </div>
  );
});
