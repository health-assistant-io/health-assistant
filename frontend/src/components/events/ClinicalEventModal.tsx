import React from 'react';
import {
  ClinicalEvent,
  createEvent,
  updateEvent,
} from '../../services/clinicalEventService';
import {
  ClinicalEventForm,
  ClinicalEventFormPayload,
} from './ClinicalEventForm';
import { Modal } from '../ui/Modal';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  event?: ClinicalEvent;
  onSuccess: (eventId: string) => void;
}

export const ClinicalEventModal: React.FC<Props> = ({ isOpen, onClose, patientId, event, onSuccess }) => {
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

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title=""
      hideHeader
      bodyClassName="p-0"
      size="lg"
    >
      <ClinicalEventForm
        patientId={patientId}
        event={event}
        showHeader
        showActions
        onCancel={onClose}
        onSubmit={handleSubmit}
      />
    </Modal>
  );
};
