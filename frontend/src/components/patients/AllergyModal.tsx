import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldAlert } from 'lucide-react';
import {
  AllergyIntolerance,
  addPatientAllergy,
  updatePatientAllergy,
} from '../../services/allergyService';
import {
  AllergyForm,
  AllergyFormPayload,
} from './AllergyForm';
import {
  createLinksFor,
  selectionsToLinkInputs,
} from '../../services/conceptEdges';
import { Modal } from '../ui/Modal';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  allergy?: AllergyIntolerance;
  onSuccess: () => void;
}

/**
 * Thin modal wrapper around the headless `AllergyForm`. The form owns the
 * draft state; this component commits the patient-instance via the canonical
 * REST endpoint and persists graph links on the picked catalog entry. The
 * shell owns the chrome.
 */
export const AllergyModal: React.FC<Props> = ({ isOpen, onClose, patientId, allergy, onSuccess }) => {
  const { t } = useTranslation();
  const formId = useId();
  const [formState, setFormState] = useState({ canSubmit: false, loading: false });

  if (!isOpen) return null;

  const handleSubmit = async (payload: AllergyFormPayload) => {
    const catalogId = payload.code.catalog_id;

    const commitPayload: any = {
      clinical_status: payload.clinical_status,
      criticality: payload.criticality,
      verification_status: payload.verification_status,
      category: payload.category,
      onset_date: payload.onset_date,
      resolved_date: payload.resolved_date,
      last_occurrence: payload.last_occurrence,
      note: payload.note,
      reactions: payload.reactions,
      code: allergy
        ? allergy.code
        : payload.code,
    };

    if (allergy) {
      await updatePatientAllergy(allergy.id, commitPayload);
    } else {
      await addPatientAllergy(patientId, commitPayload);
    }

    // Persist graph links to the allergy CATALOG entry (best-effort).
    if (payload.links.length > 0 && catalogId) {
      try {
        await createLinksFor('allergy', catalogId, selectionsToLinkInputs(payload.links));
      } catch (err) {
        console.error('Allergy graph-link persistence failed (primary write already committed)', err);
      }
    }

    onSuccess();
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={
        allergy
          ? t('allergies.modal.update_title')
          : t('allergies.modal.new_title')
      }
      description={t('allergies.modal.clinical_records', 'Clinical record')}
      headerIcon={<RecordFormIcon icon={ShieldAlert} tone="blue" />}
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={formState.canSubmit}
          loading={formState.loading}
          submitLabel={
            allergy
              ? t('allergies.modal.update_record')
              : t('allergies.modal.save_allergy')
          }
          cancelLabel={t('common.cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="lg"
    >
      <AllergyForm
        patientId={patientId}
        allergy={allergy}
        formId={formId}
        showActions={false}
        onStateChange={setFormState}
        onSubmit={handleSubmit}
        onCancel={onClose}
      />
    </Modal>
  );
};
