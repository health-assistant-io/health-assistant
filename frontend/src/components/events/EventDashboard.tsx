import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Calendar } from 'lucide-react';
import { 
  getPatientEvents, 
  getEventCategories,
  ClinicalEvent, 
  ClinicalEventStatus, 
  ClinicalEventCategory,
  deleteEvent 
} from '../../services/clinicalEventService';
import { useUIStore } from '../../store/slices/uiSlice';
import { ClinicalEventModal } from './ClinicalEventModal';
import { EventFilterToolbar } from './EventFilterToolbar';
import { DashboardEventCard } from './DashboardEventCard';
import { ResolvedEventItem } from './ResolvedEventItem';

interface Props {
  patientId: string;
}

export const EventDashboard: React.FC<Props> = ({ patientId }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [events, setEvents] = useState<ClinicalEvent[]>([]);
  const [categories, setCategories] = useState<ClinicalEventCategory[]>([]);
  const [activeCategoryId, setActiveCategoryId] = useState<string>('All');
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<ClinicalEvent | undefined>(undefined);
  const showConfirmation = useUIStore(state => state.showConfirmation);

  const fetchInitialData = async () => {
    try {
      setLoading(true);
      const [eventsData, categoriesData] = await Promise.all([
        getPatientEvents(patientId),
        getEventCategories()
      ]);
      setEvents(eventsData);
      setCategories(categoriesData);
    } catch (err) {
      console.error("Failed to fetch dashboard data", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSuccess = (eventId: string) => {
    fetchInitialData();
    navigate(`/events/${eventId}`);
  };

  useEffect(() => {
    fetchInitialData();
  }, [patientId]);

  const handleDelete = (event: ClinicalEvent) => {
    showConfirmation({
      title: t('events.delete_title'),
      message: t('events.delete_confirm', { title: event.title }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteEvent(event.id);
          fetchInitialData();
        } catch (err) {
          console.error("Failed to delete event", err);
        }
      }
    });
  };

  const filteredEvents = useMemo(() => {
    return events.filter(ev => {
      const matchesSearch = ev.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
                          ev.type_details?.name.toLowerCase().includes(searchTerm.toLowerCase());
      const matchesCategory = activeCategoryId === 'All' || ev.type_details?.category_id === activeCategoryId;
      return matchesSearch && matchesCategory;
    });
  }, [events, searchTerm, activeCategoryId]);

  const activeEvents = filteredEvents.filter(e => e.status === ClinicalEventStatus.ACTIVE);
  const historicEvents = filteredEvents.filter(e => e.status !== ClinicalEventStatus.ACTIVE);

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
    </div>
  );

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <EventFilterToolbar 
        searchTerm={searchTerm}
        setSearchTerm={setSearchTerm}
        viewMode={viewMode}
        setViewMode={setViewMode}
        activeCategoryId={activeCategoryId}
        setActiveCategoryId={setActiveCategoryId}
        categories={categories}
        onAddEvent={() => { setSelectedEvent(undefined); setIsModalOpen(true); }}
      />

      {events.length === 0 ? (
        <div className="bg-white dark:bg-dark-surface rounded-[2rem] border border-dashed border-gray-200 dark:border-dark-border p-12 text-center">
          <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-4">
             <Calendar className="w-8 h-8 text-gray-300" />
          </div>
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text mb-2">{t('events.no_events_title')}</h3>
          <p className="text-sm text-gray-500 dark:text-dark-muted max-w-sm mx-auto mb-6">
            {t('events.no_events_description')}
          </p>
          <button
            onClick={() => setIsModalOpen(true)}
            className="px-6 py-2 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-xl hover:bg-blue-100 transition-all text-sm font-bold"
          >
            {t('events.create_first_event')}
          </button>
        </div>
      ) : (
        <div className="space-y-10">
          <div className="space-y-6">
            <div className="flex items-center justify-between px-1">
              <h3 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">{t('events.active_events')} ({activeEvents.length})</h3>
            </div>
            
            <div className={viewMode === 'grid' ? "grid grid-cols-1 md:grid-cols-2 gap-6" : "space-y-4"}>
              {activeEvents.map(event => (
                <DashboardEventCard 
                  key={event.id}
                  event={event}
                  onClick={() => navigate(`/events/${event.id}`)}
                  onDelete={(e) => { e.stopPropagation(); handleDelete(event); }}
                  showDetails={viewMode === 'grid'}
                />
              ))}
              {activeEvents.length === 0 && (
                <div className="col-span-full py-8 text-center bg-gray-50 dark:bg-dark-bg/30 rounded-3xl border border-dashed border-gray-200 dark:border-dark-border">
                  <p className="text-sm text-gray-400 italic">No active events match your criteria</p>
                </div>
              )}
            </div>
          </div>

          <div className="space-y-6">
            <div className="flex items-center justify-between px-1">
              <h3 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">{t('events.resolved_history')} ({historicEvents.length})</h3>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
               {historicEvents.map(event => (
                  <ResolvedEventItem 
                    key={event.id}
                    event={event}
                    onClick={() => navigate(`/events/${event.id}`)}
                    onDelete={(e) => { e.stopPropagation(); handleDelete(event); }}
                  />

               ))}
               {historicEvents.length === 0 && (
                 <div className="col-span-full py-6 text-center bg-gray-50/50 dark:bg-dark-bg/20 rounded-2xl">
                    <p className="text-xs text-gray-400 italic font-medium">{t('events.no_history')}</p>
                 </div>
               )}
            </div>
          </div>
        </div>
      )}

      <ClinicalEventModal 
        isOpen={isModalOpen}
        onClose={() => { setIsModalOpen(false); setSelectedEvent(undefined); }}
        patientId={patientId}
        event={selectedEvent}
        onSuccess={handleSuccess}
      />
    </div>
  );
};
