import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Plus } from 'lucide-react';
import { getExaminationEvents, ClinicalEvent } from '../../services/clinicalEventService';
import { ExaminationEventModal } from './ExaminationEventModal';
import { ClinicalEventCard } from './ClinicalEventCard';

interface Props {
  examinationId: string;
  patientId?: string;
  compact?: boolean;
  isEditing?: boolean;
  readOnly?: boolean;
}

export const AssociatedEvents: React.FC<Props> = ({ 
  examinationId, 
  patientId, 
  compact = false, 
  isEditing = false,
  readOnly = false
}) => {
  const { t } = useTranslation();
  const [events, setEvents] = useState<ClinicalEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [isManagerOpen, setIsManagerOpen] = useState(false);

  const fetchEvents = async () => {
    try {
      setLoading(true);
      const data = await getExaminationEvents(examinationId);
      setEvents(data);
    } catch (err) {
      console.error("Failed to fetch associated events", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (examinationId) {
      fetchEvents();
    }
  }, [examinationId]);

  if (loading && !compact) return (
    <div className="flex items-center space-x-2 animate-pulse">
      <div className="h-4 w-4 bg-gray-200 rounded-full" />
      <div className="h-3 w-24 bg-gray-100 rounded" />
    </div>
  );

  if (events.length === 0 && !isEditing) return null;

  if (compact) {
    return (
      <div className="flex flex-wrap gap-2">
        {events.map(event => (
          <ClinicalEventCard 
            key={event.id}
            event={event}
            variant="badge"
            readOnly={readOnly}
            examinationId={examinationId}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center space-x-2">
          <Activity className="w-4 h-4 text-blue-500" />
          <h3 className="text-xs font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">
            {t('events.associated_events')}
          </h3>
          {events.length > 0 && (
            <span className="px-2 py-0.5 bg-gray-100 dark:bg-dark-bg text-gray-500 rounded-full text-[10px] font-bold">
              {events.length}
            </span>
          )}
        </div>
        
        {isEditing && patientId && !readOnly && (
          <button 
            onClick={() => setIsManagerOpen(true)}
            className="flex items-center space-x-1.5 px-3 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 transition-all font-bold text-[10px] uppercase tracking-widest border border-blue-100 dark:border-blue-800/30 shadow-sm active:scale-95"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>{t('events.manage')}</span>
          </button>
        )}
      </div>
      
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {events.map(event => (
          <ClinicalEventCard 
            key={event.id}
            event={event}
            variant="compact"
            readOnly={readOnly}
            examinationId={examinationId}
          />
        ))}
        
        {isEditing && events.length === 0 && patientId && !readOnly && (
           <button 
            onClick={() => setIsManagerOpen(true)}
            className="col-span-full py-8 border-2 border-dashed border-gray-100 dark:border-dark-border rounded-[2rem] flex flex-col items-center justify-center space-y-3 text-gray-400 hover:text-blue-50 hover:border-blue-200 transition-all group"
           >
              <div className="p-3 bg-gray-50 dark:bg-dark-bg rounded-2xl group-hover:bg-blue-50 dark:group-hover:bg-blue-900/20 transition-colors">
                <Plus className="w-6 h-6" />
              </div>
              <p className="text-xs font-bold uppercase tracking-widest">{t('events.add_to_event')}</p>
           </button>
        )}
      </div>

      {isManagerOpen && patientId && (
        <ExaminationEventModal 
          isOpen={isManagerOpen}
          onClose={() => setIsManagerOpen(false)}
          patientId={patientId}
          examinationId={examinationId}
          onSuccess={fetchEvents}
        />
      )}
    </div>
  );
};

