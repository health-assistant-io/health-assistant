import React from 'react';
import { useTranslation } from 'react-i18next';
import { Edit2, Trash2, CheckCircle2, History, Clock } from 'lucide-react';
import { ClinicalEvent, ClinicalEventStatus } from '../../services/clinicalEventService';
import { getEventIcon, getEventStatusBadge } from '../../utils/clinicalEventUtils';

interface Props {
  event: ClinicalEvent;
  onClick: () => void;
  onDelete: (e: React.MouseEvent) => void;
  showDetails?: boolean;
}

export const DashboardEventCard: React.FC<Props> = ({
  event,
  onClick,
  onDelete,
  showDetails = true
}) => {
  const { t } = useTranslation();

  return (
    <div 
      className="bg-white dark:bg-dark-surface rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm hover:shadow-md transition-all overflow-hidden group cursor-pointer"
      onClick={onClick}
    >
      <div className="p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-4">
            <div className="p-3 rounded-2xl bg-opacity-10 shadow-sm" style={{ backgroundColor: event.type_details?.color + '20', color: event.type_details?.color }}>
              {getEventIcon(event.type_details?.slug || '', "w-5 h-5")}
            </div>
            <div>
              <h4 className="text-lg font-bold text-gray-900 dark:text-dark-text leading-tight group-hover:text-blue-600 transition-colors">{event.title}</h4>
              <p className="text-[10px] text-gray-400 dark:text-dark-muted uppercase font-black tracking-widest mt-0.5">{event.type_details?.name}</p>
            </div>
          </div>
          <div className="flex items-center space-x-2" onClick={e => e.stopPropagation()}>
             {getEventStatusBadge(event.status, t, true)}
             <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
                <button onClick={onDelete} className="p-2 hover:bg-gray-50 dark:hover:bg-dark-bg rounded-lg text-red-600 transition-colors">
                  <Trash2 className="w-4 h-4" />
                </button>
             </div>
          </div>
        </div>

        {event.description && (
          <p className="mt-4 text-sm text-gray-600 dark:text-dark-muted leading-relaxed line-clamp-2 italic border-l-2 border-blue-500/20 pl-4">
            "{event.description}"
          </p>
        )}

        <div className="mt-6 grid grid-cols-3 gap-4 border-t border-gray-50 dark:border-dark-border pt-6">
          <div>
             <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest mb-1">{t('events.started')}</p>
             <p className="text-xs font-black text-gray-800 dark:text-dark-text">{event.onset_date ? new Date(event.onset_date).toLocaleDateString() : '—'}</p>
          </div>
          <div>
             <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest mb-1">{t('events.episodes')}</p>
             <p className="text-xs font-black text-gray-800 dark:text-dark-text">{event.occurrences?.length || 0}</p>
          </div>
          <div>
             <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest mb-1">{t('events.linked_visits')}</p>
             <p className="text-xs font-black text-gray-800 dark:text-dark-text">{event.examinations?.length || 0}</p>
          </div>
        </div>

        {showDetails && (
          <>
            {event.type_details?.slug === 'pregnancy' && event.event_metadata?.lmp && (
              <div className="mt-6 p-4 bg-pink-50/50 dark:bg-pink-900/10 rounded-2xl border border-pink-100 dark:border-pink-900/20">
                 <div className="flex justify-between items-center mb-2">
                   <span className="text-[10px] font-bold text-pink-600 uppercase tracking-widest">{t('events.gestational_progress')}</span>
                   <span className="text-xs font-black text-pink-700 dark:text-pink-300">
                      {Math.floor((new Date().getTime() - new Date(event.event_metadata.lmp).getTime()) / (1000 * 60 * 60 * 24 * 7))} Weeks
                   </span>
                 </div>
                 <div className="w-full h-2 bg-pink-100 dark:bg-pink-900/40 rounded-full overflow-hidden">
                    <div 
                      className="h-full bg-pink-500" 
                      style={{ width: `${Math.min(100, (Math.floor((new Date().getTime() - new Date(event.event_metadata.lmp).getTime()) / (1000 * 60 * 60 * 24 * 7)) / 40) * 100)}%` }}
                    />
                 </div>
              </div>
            )}

            {event.occurrences && event.occurrences.length > 0 && (
              <div className="mt-6 space-y-3">
                 <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">{t('events.recent_episodes')}</p>
                 <div className="space-y-2">
                    {event.occurrences.slice(-2).reverse().map((occ, i) => (
                      <div key={i} className="flex items-center justify-between p-2.5 bg-gray-50/50 dark:bg-dark-bg/50 rounded-xl border border-gray-100 dark:border-dark-border">
                         <div className="flex items-center space-x-3">
                            <div className={`w-2 h-2 rounded-full ${occ.intensity > 7 ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.4)]' : (occ.intensity > 4 ? 'bg-yellow-500 shadow-[0_0_8px_rgba(234,179,8,0.4)]' : 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.4)]')}`} />
                            <span className="text-xs font-bold text-gray-700 dark:text-dark-text">{new Date(occ.date).toLocaleDateString()}</span>
                         </div>
                         <span className="text-[10px] font-black text-gray-400">Level {occ.intensity}</span>
                      </div>
                    ))}
                 </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
