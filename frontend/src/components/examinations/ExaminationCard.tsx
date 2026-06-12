import React from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Activity, ExternalLink } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { DynamicIcon } from '../ui/DynamicIcon';
import { AIBadge } from '../ui/AIBadge';
import { getExamCategory, getCategoryStyles, stripHtml } from '../../utils/examinationUtils';
import { AssociatedEvents } from '../events/AssociatedEvents';
import { TaskProgressIndicator } from '../ui/TaskProgressIndicator';
import { CardStyles } from '../../utils/cardStyles';

interface Doctor {
  id: string;
  name: string;
}

interface Examination {
  id: string;
  patient_id: string;
  examination_date: string;
  notes: string;
  category?: string;
  category_details?: {
    name: string;
    icon?: any;
    color?: string;
  };
  doctors?: Doctor[];
  diagnoses?: string[];
  medications?: any[];
  extraction_status?: string;
  extraction_progress?: number;
  error_message?: string;
  document_statuses?: any[];
}

interface Props {
  examination: Examination;
  isSelected?: boolean;
  onClick?: () => void;
  isEditMode?: boolean;
  isSelectable?: boolean;
  onSelectToggle?: (id: string) => void;
  className?: string;
  showTechnicalDetails?: boolean;
  showExternalLink?: boolean;
  variant?: 'default' | 'compact';
  categoryIconOnly?: boolean;
  allowEventInteraction?: boolean;
}

export const ExaminationCard: React.FC<Props> = ({ 
  examination, 
  isSelected = false, 
  onClick, 
  isEditMode, 
  isSelectable,
  onSelectToggle,
  className = '',
  showTechnicalDetails = true,
  showExternalLink = false,
  variant = 'default',
  categoryIconOnly = false,
  allowEventInteraction = true
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const styles = getCategoryStyles(examination);
  const category = getExamCategory(examination);

  if (variant === 'compact') {
    return (
      <div 
        onClick={onClick}
        className={`${CardStyles.compact(isSelected)} ${className}`}
      >
        <div className={CardStyles.header}>
          <div className="flex items-center space-x-2">
            <span className={CardStyles.date(isSelected)}>
              {examination.examination_date ? new Date(examination.examination_date).toLocaleDateString() : t('common.unknown_date')}
            </span>
          </div>
          <div className="flex items-center space-x-2">
             <span 
               className={`${categoryIconOnly ? '' : styles.className} ${categoryIconOnly ? '' : '!text-[8px] px-1.5 py-0.5'} flex items-center`}
               style={categoryIconOnly ? { color: styles.style?.color } : styles.style}
               title={category}
             >
               {categoryIconOnly && examination.category_details?.icon ? (
                 <DynamicIcon icon={examination.category_details.icon as any} className="w-4 h-4" />
               ) : (
                 category
               )}
           </span>
           <div className="flex items-center space-x-1">
             <button 
               onClick={(e) => {
                 e.stopPropagation();
                 navigate(`/examinations/${examination.id}`);
               }}
               className="text-gray-400 hover:text-blue-500 transition-colors"
               title={t('common.details')}
             >
               <ExternalLink className="w-3 h-3" />
             </button>
             {showExternalLink && (
                <button 
                  onClick={(e) => {
                    e.stopPropagation();
                    window.open(`/examinations/${examination.id}`, '_blank');
                  }}
                  className="text-gray-400 hover:text-blue-500 transition-colors"
                >
                  <ExternalLink className="w-3 h-3" />
                </button>
             )}
           </div>
          </div>
        </div>
        
        <div className="flex items-center justify-between">
          <h4 className={CardStyles.title(isSelected)}>
             {examination.doctors?.length! > 0 ? `${t('doctors.dr')} ${examination.doctors![0].name}` : t('examinations.clinical_examination')}
          </h4>
          <span className={CardStyles.description}>
            {stripHtml(examination.notes) || t('examinations.no_notes')}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div 
      className={`${CardStyles.container(isSelected, isEditMode || isSelectable)} flex-shrink-0 ${className}`} 
      onClick={onClick}
    >
      <div className={CardStyles.inner}>
        {(isEditMode || isSelectable) && (
          <div className="absolute left-3 top-5 z-20" onClick={(e) => {
            e.stopPropagation();
            onSelectToggle?.(examination.id);
          }}>
             <input 
                type="checkbox" 
                className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 transition-all cursor-pointer"
                checked={isSelected}
                readOnly
              />
          </div>
        )}
        
        <div className={CardStyles.header}>
          <div className="flex items-center space-x-2">
            <span className={CardStyles.date(isSelected)}>
              {examination.examination_date ? new Date(examination.examination_date).toLocaleDateString() : t('common.unknown_date')}
            </span>
          </div>
          <div className="flex items-center space-x-2">
              <span 
                className={`${categoryIconOnly ? '' : styles.className} flex items-center gap-1`}
                style={categoryIconOnly ? { color: styles.style?.color } : styles.style}
                title={category}
              >
                  {examination.category_details?.icon && (
                    <DynamicIcon 
                      icon={examination.category_details.icon as any} 
                      className={categoryIconOnly ? "w-5 h-5" : "w-2.5 h-2.5"} 
                    />
                  )}

                {!categoryIconOnly && category}
              </span>

            {(examination.diagnoses?.length! > 0 || examination.medications?.length! > 0) && (
              <AIBadge />
            )}
              <button 
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/examinations/${examination.id}`);
                }}
                className="p-1.5 bg-white dark:bg-dark-bg text-gray-400 hover:text-blue-50 rounded-lg border border-gray-100 dark:border-dark-border transition-all shadow-sm hover:shadow-md"
                title={t('common.details')}
              >
                <ExternalLink className="w-3 h-3" />
              </button>
            </div>
          </div>
          
          <h4 className={CardStyles.title(isSelected)}>
            {examination.doctors?.length! > 0 ? `${t('doctors.dr')} ${examination.doctors![0].name}${examination.doctors!.length > 1 ? ' +' : ''}` : t('examinations.clinical_examination')}
          </h4>
          
          <div className={CardStyles.description}>
            {stripHtml(examination.notes) || t('examinations.no_notes')}
          </div>
  
          {showTechnicalDetails && (
            <>
              <div className="mt-4">
                <AssociatedEvents 
                  examinationId={examination.id} 
                  patientId={examination.patient_id} 
                  compact={true} 
                  readOnly={!allowEventInteraction}
                />
              </div>

            <div className="mt-3">
              <TaskProgressIndicator 
                examinationId={examination.id}
                examinationStatus={examination.extraction_status}
                examinationProgress={examination.extraction_progress}
                errorMessage={examination.error_message}
                documents={examination.document_statuses}
                compact={true}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
};

