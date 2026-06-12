import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { ChevronRight, Info, Edit2, ExternalLink, Calendar } from 'lucide-react';
import { ClinicalEvent } from '../../services/clinicalEventService';
import { getEventIcon, getEventStatusBadge } from '../../utils/clinicalEventUtils';
import { CardStyles } from '../../utils/cardStyles';

interface Props {
  event: ClinicalEvent;
  variant?: 'default' | 'compact' | 'badge';
  isSelected?: boolean;
  onClick?: () => void;
  readOnly?: boolean;
  examinationId?: string; // If provided, shows the reason for the link in this context
  className?: string;
}

export const ClinicalEventCard: React.FC<Props> = ({
  event,
  variant = 'default',
  isSelected = false,
  onClick,
  readOnly = false,
  examinationId,
  className = ''
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  
  const icon = getEventIcon(event.type_details?.slug || '', variant === 'badge' ? "w-4 h-4" : "w-5 h-5");
  const linkedExam = examinationId ? event.examinations?.find(e => e.examination_id === examinationId) : null;

  if (variant === 'badge') {
    const content = (
      <>
        {getEventIcon(event.type_details?.slug || '', "w-4 h-4")}
        <span>{event.title}</span>
      </>
    );
    const badgeClassName = `inline-flex items-center space-x-1.5 px-2 py-0.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border border-blue-100 dark:border-blue-800 rounded-full text-[9px] font-bold uppercase tracking-tight hover:bg-blue-100 transition-colors ${className}`;
    const title = `${event.title}${linkedExam?.reason ? `: ${linkedExam.reason}` : ''}`;

    if (readOnly) {
      return (
        <div className={badgeClassName} title={title}>
          {content}
        </div>
      );
    }

    return (
      <Link 
        to={`/events/${event.id}`}
        className={badgeClassName}
        title={title}
      >
        {content}
      </Link>
    );
  }

  if (variant === 'compact') {
    const content = (
      <>
        <div className="flex items-start space-x-3 flex-1 min-w-0">
          <div className="mt-1 p-2 rounded-xl bg-opacity-10 flex-shrink-0 transition-colors group-hover:bg-opacity-20" style={{ backgroundColor: event.type_details?.color + '20', color: event.type_details?.color }}>
            {getEventIcon(event.type_details?.slug || '', "w-4 h-4")}
          </div>
          <div className="flex-1 min-w-0 overflow-hidden">
            <h4 className="text-sm font-bold text-gray-900 dark:text-dark-text leading-tight group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">
              {event.title}
            </h4>
            <p className="text-[10px] text-gray-400 dark:text-dark-muted font-bold uppercase tracking-tight mt-0.5">
              {event.type_details?.name}
            </p>
            
            {linkedExam?.reason && (
              <div className="mt-2.5 flex items-start space-x-2.5 text-[10px] text-gray-500 dark:text-dark-muted italic bg-gray-50/50 dark:bg-dark-bg/30 p-3 rounded-2xl border border-gray-100 dark:border-dark-border/50 group-hover:border-blue-100 dark:group-hover:border-blue-900/30 transition-colors shadow-sm">
                <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-blue-400" />
                <span className="leading-relaxed break-words font-medium">{linkedExam.reason}</span>
              </div>
            )}
          </div>
        </div>
        
        {!readOnly && (
          <div className="ml-3 p-1.5 rounded-lg text-gray-300 dark:text-dark-border group-hover:text-blue-500 dark:group-hover:text-blue-400 transition-all transform group-hover:translate-x-1 flex-shrink-0 self-center">
            <ChevronRight className="w-4 h-4" />
          </div>
        )}
      </>
    );
    
    const compactClassName = `${CardStyles.compact(isSelected)} flex items-center justify-between overflow-hidden ${className} min-h-[100px] sm:min-w-[260px]`;

    if (readOnly) {
      return (
        <div className={compactClassName} onClick={onClick}>
          {content}
        </div>
      );
    }

    return (
      <Link 
        to={`/events/${event.id}`}
        className={compactClassName}
        onClick={onClick}
      >
        {content}
      </Link>
    );
  }

  // Default variant (List/Grid)
  return (
    <div 
      onClick={onClick}
      className={`${CardStyles.container(isSelected)} ${className}`}
    >
      <div className={CardStyles.inner}>
        <div className={CardStyles.header}>
          <div className="flex items-center space-x-3">
            <div 
              className={`p-2.5 rounded-xl bg-opacity-10 transition-colors ${isSelected ? 'bg-white/20' : ''}`} 
              style={!isSelected ? { backgroundColor: event.type_details?.color + '15', color: event.type_details?.color } : { color: 'white', backgroundColor: event.type_details?.color }}
            >
              {getEventIcon(event.type_details?.slug || '', "w-4 h-4")}
            </div>
            <div>
               <span className={`text-[10px] font-black uppercase tracking-widest ${isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400'}`}>
                 {event.type_details?.name}
               </span>
            </div>
          </div>
          
          <div className="flex items-center space-x-1">
            <button 
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/events/${event.id}`);
              }}
              className="p-1.5 bg-white dark:bg-dark-bg text-gray-400 hover:text-blue-500 rounded-lg border border-gray-100 dark:border-dark-border transition-all shadow-sm hover:shadow-md"
              title={t('common.details')}
            >
              <ExternalLink className="w-3 h-3" />
            </button>
          </div>
        </div>

        <h4 className={CardStyles.title(isSelected)}>
           {event.title}
        </h4>
        
        <p className={CardStyles.description}>
          {event.description || t('events.no_description')}
        </p>

        <div className="mt-4 flex items-center justify-between">
           <div className="flex items-center space-x-3 text-[10px] font-black text-gray-400 uppercase tracking-widest">
              <Calendar className="w-3 h-3" />
              <span>{event.onset_date ? new Date(event.onset_date).toLocaleDateString() : t('common.unknown_start')}</span>
           </div>
           
           <div className="flex items-center space-x-2">
              {getEventStatusBadge(event.status, t, true)}
           </div>
        </div>
      </div>
    </div>
  );
};

