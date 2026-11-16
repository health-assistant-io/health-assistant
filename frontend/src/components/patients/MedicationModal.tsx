import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pill } from 'lucide-react';
import { MedicationRecord, addPatientMedication, updatePatientMedication } from '../../services/medicationService';
import { MedicationForm, MedicationFormPayload } from './MedicationForm';
import { Modal } from '../ui/Modal';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  medication?: MedicationRecord;
  onSuccess: () => void;
}

/**
 * Thin modal wrapper around the headless `MedicationForm`. The shell owns the
 * chrome (header, one close, scrolling body, footer); the form owns the draft
 * and reports validity so the shell can drive Save.
 */
export const MedicationModal: React.FC<Props> = ({ isOpen, onClose, patientId, medication, onSuccess }) => {
  const { t } = useTranslation();
  const formId = useId();
  const [formState, setFormState] = useState({ canSubmit: false, loading: false });

  if (!isOpen) return null;

  const handleSubmit = async (payload: MedicationFormPayload) => {
    const commitPayload: any = {
      status: payload.status,
      dosage: payload.dosage,
      frequency: payload.frequency,
      start_date: payload.start_date,
      end_date: payload.end_date,
      reason: payload.reason,
      note: payload.note,
      examination_id: payload.examination_id ?? undefined,
      code: medication ? medication.code : payload.code
    };

    if (medication) {
      await updatePatientMedication(medication.id, commitPayload);
    } else {
      await addPatientMedication(patientId, commitPayload);
    }

    onSuccess();
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={
        medication
          ? t('medications.modal.update_title')
          : t('medications.modal.new_title')
      }
      description={t('medications.modal.clinical_records')}
      headerIcon={<RecordFormIcon icon={Pill} tone="blue" />}
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={formState.canSubmit}
          loading={formState.loading}
          submitLabel={
            medication
              ? t('medications.modal.update_record')
              : t('medications.modal.save_medication')
          }
          cancelLabel={t('common.cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="lg"
    >
      <MedicationForm
        patientId={patientId}
        medication={medication}
        formId={formId}
        showActions={false}
        onStateChange={setFormState}
        onSubmit={handleSubmit}
        onCancel={onClose}
      />
    </Modal>
  );
};
