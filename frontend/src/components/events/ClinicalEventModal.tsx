import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity } from 'lucide-react';
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
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  event?: ClinicalEvent;
  onSuccess: (eventId: string) => void;
}

/**
 * Modal shell for `ClinicalEventForm`. The selected event type/category is
 * surfaced by the form's own context bar in the body; the shell header stays
 * static so it never has to mirror form state.
 */
export const ClinicalEventModal: React.FC<Props> = ({ isOpen, onClose, patientId, event, onSuccess }) => {
  const { t } = useTranslation();
  const formId = useId();
  const [formState, setFormState] = useState({ canSubmit: false, loading: false });

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
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={event ? t('events.update_title') : t('events.new_title')}
      description={t('events.clinical_records', 'Clinical patient records')}
      headerIcon={<RecordFormIcon icon={Activity} tone="blue" />}
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={formState.canSubmit}
          loading={formState.loading}
          submitLabel={
            event ? t('events.update_event') : t('events.save_event')
          }
          cancelLabel={t('common.cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="lg"
    >
      <ClinicalEventForm
        patientId={patientId}
        event={event}
        formId={formId}
        showActions={false}
        onStateChange={setFormState}
        onCancel={onClose}
        onSubmit={handleSubmit}
      />
    </Modal>
  );
};
