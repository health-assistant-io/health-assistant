import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, Link } from 'react-router-dom';
import { 
  ClinicalEvent, 
  getEventCategories,
  ClinicalEventCategory
} from '../../services/clinicalEventService';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { 
  Plus, Search, Activity, LayoutGrid, List as ListIcon,
  Edit2, ExternalLink, Calendar, ChevronRight
} from 'lucide-react';
import api from '../../api/axios';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { ClinicalEventModal } from '../../components/events/ClinicalEventModal';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { ClinicalEventCard } from '../../components/events/ClinicalEventCard';
import { ExaminationCard } from '../../components/examinations/ExaminationCard';
import { getEventIcon, getEventStatusBadge } from '../../utils/clinicalEventUtils';
import { MasterDetailLayout } from '../../components/ui/MasterDetailLayout';
import { useMasterDetail } from '../../hooks/useMasterDetail';
import { PageContainer } from '../../components/ui/PageContainer';
import { CategoryDropdown } from '../../components/ui/CategoryDropdown';
import { useCreateIntent } from '../../hooks/useCreateIntent';

export const ClinicalEventList = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const [events, setEvents] = useState<ClinicalEvent[]>([]);
  const [categories, setCategories] = useState<ClinicalEventCategory[]>([]);
  const [selectedCategories, setSelectedCategories] = useState<string[]>(['All']);
  const [loading, setLoading] = useState(true);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [selectedEvent, setSelectedEvent] = useState<ClinicalEvent | null>(null);
  const [viewMode, setViewMode] = useState<'list' | 'grid'>('list');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingEvent, setEditingEvent] = useState<ClinicalEvent | undefined>(undefined);

  const { isLargeScreen, handleItemClick, containerRef } = useMasterDetail({
    detailPath: (id) => `/events/${id}`,
    onSelect: (id) => {
      const event = events.find(e => e.id === id);
      if (event) setSelectedEvent(event);
    }
  });
  
  const fetchInitialData = async () => {
    try {
      setLoading(true);
      const [eventsResponse, categoriesData] = await Promise.all([
        api.get(currentPatient ? `/clinical-events?patient_id=${currentPatient.id}` : '/clinical-events'),
        getEventCategories()
      ]);
      
      const eventsData = eventsResponse.data;
      setEvents(eventsData);
      setCategories(categoriesData);
      
      if (eventsData.length > 0 && isLargeScreen) setSelectedEvent(eventsData[0]);
      else setSelectedEvent(null);
    } catch (err) {
      console.error("Failed to fetch initial data", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchInitialData();
  }, [currentPatient?.id]);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const fetchEvents = async () => {
    try {
      const url = currentPatient ? `/clinical-events?patient_id=${currentPatient.id}` : '/clinical-events';
      const response = await api.get(url);
      setEvents(response.data);
    } catch (err) {
      console.error("Failed to fetch clinical events", err);
    }
  };

  const handleSuccess = (eventId: string) => {
    fetchEvents();
    navigate(`/events/${eventId}`);
  };

  const handleCreateNew = () => {
    if (!currentPatient) {
      navigate('/patients');
      return;
    }
    setEditingEvent(undefined);
    setIsModalOpen(true);
  };

  // Open the create modal automatically when arrived via ?new=event
  useCreateIntent(handleCreateNew, 'event');

  const filteredEvents = events.filter(ev => {
    const matchesSearch = 
      ev.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
      ev.type_details?.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      ev.description?.toLowerCase().includes(searchTerm.toLowerCase());
    
    const matchesCategory = 
      selectedCategories.includes('All') || 
      selectedCategories.includes(ev.type_details?.category_concept_id || '');
    
    return matchesSearch && matchesCategory;
  });

  if (!currentPatient) return <NoPatientState icon={Activity} contextKey="events" />;

  if (loading) return <LoadingState variant="section" showText={true} />;

  const nonEmptyCategories = categories.filter(cat => 
    events.some(e => e.type_details?.category_concept_id === cat.id)
  );

  const tabsWithCounts = [
    { name: t('common.view_all') as string, id: 'All', count: events.length, icon: null, color: null },
    ...nonEmptyCategories.map(cat => ({
      name: t(`categories.${cat.name}`, cat.name) as string, // Attempt translation or fallback to name
      id: cat.id,
      count: events.filter(e => e.type_details?.category_concept_id === cat.id).length,
      icon: getEventIcon(cat.slug),
      color: cat.color || null
    }))
  ];

  const Sidebar = null;

  const toggleCategory = (categoryId: string) => {
    setSelectedCategories(prev => {
      if (categoryId === 'All') return ['All'];
      const filtered = prev.filter(c => c !== 'All');
      if (filtered.includes(categoryId)) {
        const next = filtered.filter(c => c !== categoryId);
        return next.length === 0 ? ['All'] : next;
      }
      return [...filtered, categoryId];
    });
  };

  const ListHeader = (
    <>
        <h3 className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">
          {t('events.showing_events', { count: filteredEvents.length, defaultValue: `Showing ${filteredEvents.length} events` })}
        </h3>
        <div className="flex items-center space-x-2">
           <div className="flex items-center bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-lg p-1">
              <button 
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-md transition-all ${viewMode === 'grid' ? 'bg-gray-100 dark:bg-dark-bg shadow-sm text-blue-600' : 'text-gray-400 hover:text-gray-600'}`}
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button 
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded-md transition-all ${viewMode === 'list' ? 'bg-gray-100 dark:bg-dark-bg shadow-sm text-blue-600' : 'text-gray-400 hover:text-gray-600'}`}
              >
                <ListIcon className="w-4 h-4" />
              </button>
           </div>
        </div>
    </>
  );

  const List = (
      <div className={`
        ${viewMode === 'grid' ? 'grid grid-cols-1 sm:grid-cols-2 gap-4' : 'flex flex-col space-y-4'} 
      `}>
          {filteredEvents.map((event) => (
            <ClinicalEventCard 
              key={event.id}
              event={event}
              isSelected={selectedEvent?.id === event.id}
              onClick={() => handleItemClick(event.id, event)}
            />
          ))}
      </div>
  );

  const Preview = selectedEvent ? (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="p-8 border-b border-gray-50 dark:border-dark-border flex-shrink-0">
         <div className="flex items-start justify-between mb-6">
             <div className="flex items-center space-x-4">
                <div 
                 className="p-3 rounded-2xl bg-opacity-10" 
                 style={{ backgroundColor: selectedEvent.type_details?.color + '20', color: selectedEvent.type_details?.color }}
                >
                   {getEventIcon(selectedEvent.type_details?.slug || '', "w-5 h-5")}
                </div>
                 <div>
                    <Link to={`/events/${selectedEvent.id}`} className="group/title">
                       <h3 className="text-xl font-black text-gray-900 dark:text-dark-text tracking-tight uppercase group-hover/title:text-blue-600 transition-colors">{selectedEvent.title}</h3>
                    </Link>
                    <div className="flex items-center space-x-2 text-[10px] font-black text-gray-400 uppercase tracking-widest mt-0.5">
                       <Calendar className="w-3 h-3" />
                       <span>{selectedEvent.onset_date ? new Date(selectedEvent.onset_date).toLocaleDateString(undefined, { dateStyle: 'long' }) : 'Unknown start'}</span>
                    </div>
                 </div>
              </div>
               <div className="flex flex-col items-end space-y-4">
                  {getEventStatusBadge(selectedEvent.status, t, true)}
                  <div className="flex items-center space-x-2">
                     <button 
                       onClick={() => navigate(`/events/${selectedEvent.id}`)}
                       className="p-3 bg-gray-50 dark:bg-dark-bg text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 rounded-2xl transition-all hover:shadow-md"
                       title={t('common.details')}
                     >
                       <ExternalLink className="w-5 h-5" />
                     </button>
                  </div>
               </div>

          </div>
       </div>

      <div className="flex-1 overflow-y-auto p-8 space-y-8 custom-scrollbar">
         <div className="space-y-4">
             <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.3em] ml-1">{t('events.overview', 'Event Overview')}</h4>
             <div className="bg-gray-50/50 dark:bg-dark-bg/30 p-6 rounded-3xl border border-gray-100 dark:border-dark-border text-sm text-gray-700 dark:text-dark-text leading-relaxed italic prose prose-sm dark:prose-invert max-w-none">
                {selectedEvent.description || t('events.no_description', 'No detailed narrative provided for this event.')}
             </div>
         </div>

         {selectedEvent.occurrences && selectedEvent.occurrences.length > 0 && (
            <div className="space-y-4 animate-in fade-in slide-in-from-top-4">
               <h4 className="text-[10px] font-black text-blue-600 dark:text-blue-400 uppercase tracking-[0.3em] ml-1">{t('events.recent_activity', 'Recent Activity')}</h4>
               <div className="space-y-3">
                 {selectedEvent.occurrences.slice(-3).reverse().map((occ, i) => (
                   <div key={i} className="flex items-center justify-between p-4 bg-gray-50 dark:bg-dark-bg/20 rounded-2xl border border-gray-100 dark:border-dark-border">
                      <div className="flex items-center space-x-3">
                         <div className={`w-2 h-2 rounded-full ${occ.intensity > 7 ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]' : (occ.intensity > 4 ? 'bg-yellow-500' : 'bg-green-500')}`} />
                         <span className="text-xs font-bold text-gray-700 dark:text-dark-text">{new Date(occ.date).toLocaleDateString()}</span>
                         {occ.location && <span className="px-2 py-0.5 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-lg text-[10px] text-gray-500 font-bold uppercase">{occ.location}</span>}
                      </div>
                      <span className="text-[10px] font-black text-gray-400">Lv. {occ.intensity}</span>
                   </div>
                 ))}
              </div>
           </div>
         )}

           <div className="space-y-4">
             <h4 className="text-[10px] font-black text-indigo-600 dark:text-indigo-400 uppercase tracking-[0.3em] ml-1">{t('events.connected_visits', 'Connected Visits')}</h4>
             <div className="space-y-3">
               {selectedEvent.examinations && selectedEvent.examinations.length > 0 ? (
                 selectedEvent.examinations.map((link, idx) => (
                   <ExaminationCard 
                      key={idx}
                      examination={link}
                      variant="compact"
                      showTechnicalDetails={false}
                      showExternalLink={true}
                      allowEventInteraction={false}
                      onClick={() => window.open(`/examinations/${link.id}`, '_blank')}
                   />
                 ))
                 ) : (
                 <p className="text-xs text-gray-400 italic py-4 text-center">{t('events.no_linked_visits')}</p>
               )}
            </div>
          </div>
      </div>
    </div>
  ) : (
    <div className="h-full flex flex-col items-center justify-center p-10 text-center opacity-30">
       <div className="w-20 h-20 bg-gray-100 dark:bg-dark-bg rounded-full flex items-center justify-center mb-6">
          <Activity className="w-10 h-10" />
       </div>
       <p className="text-lg font-black uppercase tracking-widest">{t('events.select_to_preview', 'Select an event to preview')}</p>
    </div>
  );

  return (
    <PageContainer>
      <PageHeader
        title={t('events.title')}
        subtitle={t('events.subtitle', 'Global Health Events')}
        icon={<Activity className="w-8 h-8" />}
        breadcrumbs={[]}
      />

      <StickyToolbar
        className="flex-col sm:flex-row items-stretch sm:items-center"
        actions={
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full lg:w-auto flex-shrink-0 pt-2 sm:pt-0">
            <button 
              onClick={handleCreateNew}
              className="w-full sm:w-auto flex items-center justify-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95 whitespace-nowrap"
            >
              <Plus className="w-5 h-5" />
              <span className="hidden sm:inline">{t('events.record_new_event')}</span>
            </button>
          </div>
        }
      >
          <CategoryDropdown 
             tabs={tabsWithCounts} 
             selectedCategories={selectedCategories} 
             onToggleCategory={toggleCategory} 
             label={t('events.categories')}
             allLabel={t('common.view_all')}
          />
      </StickyToolbar>

      <MasterDetailLayout 
        list={List}
        listHeader={ListHeader}
        detail={Preview}
        listWidth="lg:w-[400px] xl:w-[500px]"
        containerRef={containerRef}
        showDetail={isLargeScreen}
      />

      {currentPatient && (
        <ClinicalEventModal 
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          patientId={currentPatient.id}
          event={editingEvent}
          onSuccess={handleSuccess}
        />
      )}
    </PageContainer>
  );
};

export default ClinicalEventList;
