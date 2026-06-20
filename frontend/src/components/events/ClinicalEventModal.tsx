import React from 'react';
import { createPortal } from 'react-dom';
import {
  ClinicalEvent,
  createEvent,
  updateEvent,
} from '../../services/clinicalEventService';
import {
  ClinicalEventForm,
  ClinicalEventFormPayload,
} from './ClinicalEventForm';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  event?: ClinicalEvent;
  onSuccess: (eventId: string) => void;
}

export const ClinicalEventModal: React.FC<Props> = ({ isOpen, onClose, patientId, event, onSuccess }) => {
  if (!isOpen) return null;

  const handleSubmit = async (payload: ClinicalEventFormPayload) => {
    try {
      const savedEvent = event ? await updateEvent(event.id, payload) : await createEvent(payload);
      onSuccess(savedEvent.id);
      onClose();
    } catch (err) {
      console.error('Failed to save event', err);
      throw err;
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div
        className="bg-white dark:bg-dark-surface w-full max-w-4xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh]"
        onClick={e => e.stopPropagation()}
      >
        <ClinicalEventForm
          patientId={patientId}
          event={event}
          showHeader
          showActions
          onCancel={onClose}
          onSubmit={handleSubmit}
        />
      </div>
    </div>,
    document.body
  );
};
