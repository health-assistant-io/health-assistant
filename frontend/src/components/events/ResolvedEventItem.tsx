import React from 'react';
import { useTranslation } from 'react-i18next';
import { Edit2, Trash2, History, CheckCircle2, Clock } from 'lucide-react';
import { ClinicalEvent, ClinicalEventStatus } from '../../services/clinicalEventService';
import { getEventIcon, getEventStatusBadge } from '../../utils/clinicalEventUtils';

interface Props {
  event: ClinicalEvent;
  onClick: () => void;
  onDelete: (e: React.MouseEvent) => void;
}

export const ResolvedEventItem: React.FC<Props> = ({
  event,
  onClick,
  onDelete
}) => {
  const { t } = useTranslation();

  return (
    <div 
      className="bg-white dark:bg-dark-surface/50 rounded-3xl border border-gray-100 dark:border-dark-border p-5 opacity-80 grayscale-[0.5] hover:grayscale-0 hover:opacity-100 transition-all flex items-center justify-between group cursor-pointer"
      onClick={onClick}
    >
      <div className="flex items-center space-x-4">
         <div className="p-2.5 rounded-xl bg-gray-50 dark:bg-dark-bg text-gray-400 shadow-sm group-hover:text-blue-500 transition-colors">
            {getEventIcon(event.type_details?.slug || '', "w-5 h-5")}
         </div>
         <div>
            <h4 className="text-sm font-bold text-gray-900 dark:text-dark-text leading-tight group-hover:text-blue-600 transition-colors">{event.title}</h4>
            <p className="text-[10px] text-gray-400 dark:text-dark-muted uppercase font-black tracking-tight mt-0.5">
               {event.type_details?.name} • {event.onset_date ? new Date(event.onset_date).getFullYear() : '—'}
            </p>
         </div>
      </div>
      <div className="flex items-center space-x-3" onClick={e => e.stopPropagation()}>
         <div className="hidden group-hover:flex items-center space-x-1">
            <button onClick={onDelete} className="p-1.5 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-lg text-red-600 transition-colors">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
         </div>
         {getEventStatusBadge(event.status, t, true)}
      </div>
    </div>
  );
};
