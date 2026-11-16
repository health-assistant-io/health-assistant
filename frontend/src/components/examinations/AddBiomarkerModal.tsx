import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FlaskConical } from 'lucide-react';
import { AddBiomarkerForm } from './AddBiomarkerForm';
import { createObservation } from '../../services/observationService';
import { Modal } from '../ui/Modal';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  examinationId: string;
  onSuccess: () => void;
}

/**
 * Thin popup wrapper around the headless AddBiomarkerForm. The form logic
 * (catalog search, FHIR Observation build, units) lives in AddBiomarkerForm so
 * it can be reused by the HITL AddBiomarkerHandler without a portal of its own.
 */
export const AddBiomarkerModal: React.FC<Props> = ({
  isOpen,
  onClose,
  patientId,
  examinationId,
  onSuccess
}) => {
  const { t } = useTranslation();
  const formId = useId();
  const [formState, setFormState] = useState({ canSubmit: false, loading: false });

  if (!isOpen) return null;

  const handleSubmit = async (observation: Parameters<React.ComponentProps<typeof AddBiomarkerForm>['onSubmit']>[0]) => {
    await createObservation(observation as any);
    onSuccess();
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={t('examination_detail.add_biomarker.title')}
      description={t('examination_detail.add_biomarker.manual_entry')}
      headerIcon={<RecordFormIcon icon={FlaskConical} tone="blue" />}
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={formState.canSubmit}
          loading={formState.loading}
          submitLabel={t('examination_detail.add_biomarker.add_result')}
          cancelLabel={t('common.cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="md"
    >
      <AddBiomarkerForm
        patientId={patientId}
        examinationId={examinationId}
        onSubmit={handleSubmit}
        onCancel={onClose}
        formId={formId}
        showActions={false}
        onStateChange={setFormState}
      />
    </Modal>
  );
};
