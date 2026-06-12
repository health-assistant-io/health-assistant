import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X, Search, Plus, Save, Activity, Check } from 'lucide-react';
import { 
  getPatientEvents, 
  ClinicalEvent, 
  updateEvent
} from '../../services/clinicalEventService';
import { ClinicalEventModal } from './ClinicalEventModal';
import { getEventIcon } from '../../utils/clinicalEventUtils';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  examinationId: string;
  onSuccess: () => void;
}

export const ExaminationEventModal: React.FC<Props> = ({ isOpen, onClose, patientId, examinationId, onSuccess }) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [allEvents, setAllEvents] = useState<ClinicalEvent[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedEventIds, setSelectedEventIds] = useState<string[]>([]);
  const [eventReasons, setEventReasons] = useState<Record<string, string>>({});
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  const fetchEvents = async () => {
    try {
      setLoading(true);
      const data = await getPatientEvents(patientId);
      setAllEvents(data);
      
      // Initialize selected events based on existing links to this examination
      const linked = data.filter(ev => ev.examinations?.some(ex => ex.examination_id === examinationId));
      setSelectedEventIds(linked.map(ev => ev.id));
      
      const reasons: Record<string, string> = {};
      linked.forEach(ev => {
        const link = ev.examinations.find(ex => ex.examination_id === examinationId);
        reasons[ev.id] = link?.reason || '';
      });
      setEventReasons(reasons);
    } catch (err) {
      console.error("Failed to fetch events for linking", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchEvents();
    }
  }, [isOpen, patientId, examinationId]);

  const handleToggleEvent = (eventId: string) => {
    setSelectedEventIds(prev => 
      prev.includes(eventId) ? prev.filter(id => id !== eventId) : [...prev, eventId]
    );
  };

  const handleReasonChange = (eventId: string, reason: string) => {
    setEventReasons(prev => ({ ...prev, [eventId]: reason }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      // We need to update each event's examination list
      for (const event of allEvents) {
        const isSelected = selectedEventIds.includes(event.id);
        const wasSelected = event.examinations?.some(ex => ex.examination_id === examinationId);
        
        if (isSelected || wasSelected) {
          // Calculate new examinations list for this event
          let newExams = event.examinations.map(ex => ({
            examination_id: ex.examination_id,
            reason: ex.reason
          }));

          if (isSelected) {
            const existingIdx = newExams.findIndex(ex => ex.examination_id === examinationId);
            if (existingIdx >= 0) {
              newExams[existingIdx].reason = eventReasons[event.id] || '';
            } else {
              newExams.push({ examination_id: examinationId, reason: eventReasons[event.id] || '' });
            }
          } else {
            newExams = newExams.filter(ex => ex.examination_id !== examinationId);
          }

          // Only update if changed
          await updateEvent(event.id, { examinations: newExams });
        }
      }
      onSuccess();
      onClose();
    } catch (err) {
      console.error("Failed to save event associations", err);
    } finally {
      setSaving(false);
    }
  };

  const filteredEvents = allEvents.filter(ev => 
    ev.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
    ev.type_details?.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-[2.5rem] shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[85vh]" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
              <Activity className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">
                {t('events.manage_associations')}
              </h2>
              <p className="text-[10px] text-gray-500 dark:text-dark-muted font-bold uppercase tracking-widest mt-0.5">
                {t('events.link_to_this_visit')}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="flex-1 overflow-hidden flex flex-col p-8 space-y-6">
          <div className="flex items-center justify-between gap-4">
             <div className="relative flex-1 group">
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 group-focus-within:text-blue-500 transition-colors" />
                <input 
                  type="text"
                  placeholder={t('events.search_events_placeholder')}
                  className="w-full pl-11 pr-4 py-3 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-2xl text-sm focus:ring-2 focus:ring-blue-500/20 outline-none transition-all"
                  value={searchTerm}
                  onChange={e => setSearchTerm(e.target.value)}
                />
             </div>
             <button 
                onClick={() => setIsCreateModalOpen(true)}
                className="flex items-center space-x-2 px-4 py-3 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-2xl hover:bg-blue-100 transition-all font-bold text-xs uppercase tracking-widest whitespace-nowrap"
             >
                <Plus className="w-4 h-4" />
                <span>{t('events.create_new')}</span>
             </button>
          </div>

          <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar space-y-3">
             {loading ? (
               <div className="flex flex-col items-center justify-center py-12 space-y-4">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
                  <p className="text-xs font-bold text-gray-400 uppercase tracking-widest">{t('common.loading')}</p>
               </div>
             ) : filteredEvents.length === 0 ? (
               <div className="py-12 text-center">
                  <p className="text-sm text-gray-400 italic">{t('events.no_events_found')}</p>
               </div>
             ) : (
               filteredEvents.map(event => {
                 const isSelected = selectedEventIds.includes(event.id);
                 return (
                   <div 
                    key={event.id}
                    className={`rounded-3xl border transition-all ${
                      isSelected 
                        ? 'bg-blue-50/30 dark:bg-blue-900/10 border-blue-200' 
                        : 'bg-white dark:bg-dark-bg/50 border-gray-100 dark:border-dark-border hover:border-blue-100'
                    }`}
                   >
                     <div 
                      className="p-4 flex items-center justify-between cursor-pointer"
                      onClick={() => handleToggleEvent(event.id)}
                     >
                        <div className="flex items-center space-x-4">
                           <div 
                            className="p-3 rounded-2xl bg-opacity-10" 
                            style={{ backgroundColor: event.type_details?.color + '20', color: event.type_details?.color }}
                           >
                              {getEventIcon(event.type_details?.slug || '', "w-5 h-5")}
                           </div>
                           <div>
                              <h4 className="text-sm font-bold text-gray-900 dark:text-dark-text">{event.title}</h4>
                              <p className="text-[10px] text-gray-400 uppercase font-black tracking-widest">{event.type_details?.name}</p>
                           </div>
                        </div>
                        <div className={`w-6 h-6 rounded-full border-2 flex items-center justify-center transition-all ${isSelected ? 'bg-blue-600 border-blue-600 text-white' : 'border-gray-200'}`}>
                           {isSelected && <Check className="w-3.5 h-3.5" />}
                        </div>
                     </div>
                     
                     {isSelected && (
                       <div className="px-4 pb-4 animate-in slide-in-from-top-2 duration-200">
                          <label className="block text-[9px] font-black text-blue-600 uppercase tracking-widest mb-1.5 ml-1">{t('events.reason_for_visit_label')}</label>
                          <input 
                            type="text"
                            placeholder={t('events.reason_for_visit_placeholder')}
                            className="w-full px-4 py-2.5 bg-white dark:bg-dark-surface border border-blue-100 dark:border-blue-900/50 rounded-xl text-xs focus:ring-1 focus:ring-blue-500 outline-none"
                            value={eventReasons[event.id] || ''}
                            onChange={e => handleReasonChange(event.id, e.target.value)}
                            onClick={e => e.stopPropagation()}
                          />
                       </div>
                     )}
                   </div>
                 );
               })
             )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-8 py-6 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center justify-end space-x-4">
          <button
            type="button"
            onClick={onClose}
            className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || loading}
            className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2"
          >
            {saving ? (
              <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>{t('common.save')}</span>
          </button>
        </div>
      </div>

      <ClinicalEventModal 
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        patientId={patientId}
        onSuccess={() => {
          setIsCreateModalOpen(false);
          fetchEvents();
        }}
      />
    </div>,
    document.body
  );
};
