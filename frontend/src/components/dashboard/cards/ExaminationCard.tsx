import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  X, 
  FileText
} from 'lucide-react';
import { useBiomarkers } from '../../../hooks/useBiomarkers';
import { BiomarkerObservation } from '../../../types/biomarker';
import { isAbnormal, formatUnit } from '../../../utils/biomarkerUtils';

export const ExaminationCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children, data, documents
  } = props;
  
  const title = data?.category || t('dashboard.cards.examination_note');
  const doctor = data?.has_assigned_doctor ? data?.doctor_name : null;
  const date = data?.examination_date || "No date";
  const notes = data?.notes || "No clinical notes available for this patient.";

  // Use the new hook to get biomarkers for this specific examination if documents are provided
  const examDocs = (documents || []).filter((d: any) => d.examination_id === data?.id);
  const { groupByCategory } = useBiomarkers({ documents: examDocs });
  const biomarkers = Object.values(groupByCategory()).map((group: BiomarkerObservation[]) => group[0]).slice(0, 3);

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
      <div className="flex items-center space-x-2 mb-4">
        <FileText className="w-5 h-5 text-blue-500" />
        <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{title}</h3>
      </div>
      <div className="flex-1 overflow-y-auto custom-scrollbar pr-1">
        <div className="flex flex-col justify-between h-full">
          <div className="space-y-4">
            <div className="flex justify-between items-start mb-2">
              <div>
                {doctor && <h4 className="font-bold text-gray-900 dark:text-dark-text">{doctor}</h4>}
                <p className="text-xs text-gray-500 dark:text-dark-muted">{doctor ? t('examinations.attending_physician') : t('examinations.medical_record')}</p>
              </div>
              <span className="px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded">{date}</span>
            </div>

            <p className="text-sm text-gray-500 dark:text-dark-muted italic line-clamp-2">
              "{notes}"
            </p>

            {biomarkers.length > 0 && (
              <div className="space-y-2">
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('examinations.key_biomarkers')}</p>
                <div className="flex flex-wrap gap-2">
                  {biomarkers.map((b) => (
                    <div key={b.id} className="bg-gray-50 dark:bg-dark-bg px-2 py-1 rounded-lg border border-gray-100 dark:border-dark-border flex items-center space-x-2">
                      <span className="text-[10px] font-bold text-gray-700 dark:text-dark-text">{b.displayName}:</span>
                      <span className={`text-[10px] font-black ${isAbnormal(b.interpretation) ? 'text-red-500' : 'text-blue-600 dark:text-blue-400'}`}>
                        {b.value.raw} <span className="text-[8px] font-bold opacity-60 uppercase">{formatUnit(b.unit.rawSymbol)}</span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          
          <button 
            onClick={() => data?.id && navigate(`/examinations/${data.id}`)}
            className="w-full mt-4 py-2 bg-gray-50 dark:bg-dark-bg hover:bg-gray-100 dark:hover:bg-dark-border text-gray-700 dark:text-dark-text text-sm font-medium rounded-lg transition-colors"
          >
            {t('examinations.analyze_report')}
          </button>
        </div>
      </div>
      {children}
    </div>
  );
});
