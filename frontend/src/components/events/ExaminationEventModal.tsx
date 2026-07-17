import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Activity } from 'lucide-react';
import {
  getPatientEvents,
  ClinicalEvent,
  updateEvent
} from '../../services/clinicalEventService';
import { FormModal } from '../ui/FormModal';
import { InstanceField } from '../instances/InstanceField';
import '../../features/instances/adapters'; // registers the instance adapters
import { ClinicalEventModal } from './ClinicalEventModal';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  examinationId: string;
  onSuccess: () => void;
}

export const ExaminationEventModal: React.FC<Props> = ({ isOpen, onClose, patientId, examinationId, onSuccess }) => {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [allEvents, setAllEvents] = useState<ClinicalEvent[]>([]);
  const [selectedEventIds, setSelectedEventIds] = useState<string[]>([]);
  const [eventReasons, setEventReasons] = useState<Record<string, string>>({});
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  const fetchEvents = async () => {
    try {
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
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchEvents();
    }
  }, [isOpen, patientId, examinationId]);

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

  if (!isOpen) return null;

  return (
    <>
      <FormModal
        isOpen={isOpen}
        onClose={onClose}
        title={t('events.manage_associations')}
        icon={
          <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
            <Activity className="w-6 h-6 text-blue-600 dark:text-blue-400" />
          </div>
        }
        onSubmit={handleSave}
        submitting={saving}
        submitLabel={t('common.save')}
        cancelLabel={t('common.cancel')}
        bodyClassName="p-6 space-y-4"
      >
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setIsCreateModalOpen(true)}
            className="flex items-center space-x-2 px-4 py-3 bg-gray-50 dark:bg-dark-bg text-gray-500 dark:text-dark-muted rounded-2xl hover:bg-gray-100 transition-all font-bold text-xs uppercase tracking-widest whitespace-nowrap"
          >
            <Plus className="w-4 h-4" />
            <span>{t('events.create_new')}</span>
          </button>
        </div>

        <InstanceField
          label={t('events.associated_events', 'Associated events')}
          allowedTypes={['event']}
          patientId={patientId}
          mode="multi"
          displayMode="cards"
          value={selectedEventIds.map(id => ({ type: 'event', id }))}
          onChange={(next) => setSelectedEventIds(next.map(s => s.id))}
          renderCardFooter={(sel) => (
            <input
              type="text"
              placeholder={t('events.reason_for_visit_placeholder')}
              className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-xl text-[11px] font-semibold focus:ring-4 focus:ring-blue-500/10 outline-none transition-all placeholder:font-medium dark:text-dark-text"
              value={eventReasons[sel.id] || ''}
              onChange={e => handleReasonChange(sel.id, e.target.value)}
            />
          )}
        />
      </FormModal>

      <ClinicalEventModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        patientId={patientId}
        onSuccess={() => {
          setIsCreateModalOpen(false);
          fetchEvents();
        }}
      />
    </>
  );
};
