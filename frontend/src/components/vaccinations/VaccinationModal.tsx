/**
 * VaccinationModal — Modal host for {@link VaccinationForm}.
 *
 * Owns the API writes so the form stays a pure controlled component:
 *  - add / edit a patient immunization (`/vaccines/*`)
 *  - persist graph links on the vaccine catalog entry via `createLinksFor`
 *    (`srcType="immunization"`) after the primary record is committed
 *  (the vaccine definition itself always exists — it is picked from the
 *  catalog or created from the browse dialog before this modal commits)
 *
 * The popup shell owns the chrome: header (icon · title · description · one
 * close), the scrolling body and the footer. The form renders fields only and
 * reports validity/loading so the shell can drive its Save button.
 */
import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Syringe } from 'lucide-react';
import { Modal } from '../ui/Modal';
import {
  VaccinationForm,
  type VaccinationFormPayload,
} from './VaccinationForm';
import { RecordFormFooter } from '../records/RecordFormFooter';
import { RecordFormIcon } from '../records/RecordFormIcon';
import type { PatientImmunization } from '../../types/vaccine';
import { addPatientImmunization, updatePatientImmunization } from '../../services/vaccineService';
import {
  createLinksFor,
  selectionsToLinkInputs,
} from '../../services/conceptEdges';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  immunization?: PatientImmunization;
  onSuccess: () => void;
}

export const VaccinationModal: React.FC<Props> = ({
  isOpen,
  onClose,
  patientId,
  immunization,
  onSuccess,
}) => {
  const { t } = useTranslation();
  const formId = useId();
  const [formState, setFormState] = useState({ canSubmit: false, loading: false });

  if (!isOpen) return null;

  const handleSubmit = async (payload: VaccinationFormPayload) => {
    const catalogId = payload.vaccine_code.catalog_id ?? null;

    const commit: Partial<PatientImmunization> = {
      status: payload.status as PatientImmunization['status'],
      examination_id: payload.examination_id ?? null,
      administered_at: payload.administered_at ?? null,
      dose_number: payload.dose_number ?? null,
      lot_number: payload.lot_number ?? null,
      manufacturer: payload.manufacturer ?? null,
      location: payload.location ?? null,
      note: payload.note ?? null,
      vaccine_code: immunization
        ? immunization.vaccine_code
        : payload.vaccine_code,
    };

    if (immunization) {
      await updatePatientImmunization(immunization.id, commit);
    } else {
      await addPatientImmunization(patientId, {
        vaccine_catalog_id: catalogId,
        ...commit,
        vaccine_code: {
          text: payload.vaccine_code.text,
          catalog_id: catalogId ?? null,
        },
      });
    }

    // Persist graph links on the catalog entry (best-effort — a link failure
    // must not roll back the record). Only when we have a catalog id.
    const linkInputs = selectionsToLinkInputs(payload.links);
    if (catalogId && linkInputs.length > 0) {
      await createLinksFor('immunization', catalogId, linkInputs, {
        source: 'manual',
      });
    }

    onSuccess();
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title={
        immunization
          ? t('vaccinations.modal.update_title')
          : t('vaccinations.modal.new_title')
      }
      description={t('vaccinations.modal.subtitle')}
      headerIcon={<RecordFormIcon icon={Syringe} tone="rose" />}
      closeLabel={t('common.close')}
      footer={
        <RecordFormFooter
          formId={formId}
          canSubmit={formState.canSubmit}
          loading={formState.loading}
          submitLabel={
            immunization
              ? t('vaccinations.modal.update_record')
              : t('vaccinations.modal.save')
          }
          cancelLabel={t('common.cancel')}
          onCancel={onClose}
        />
      }
      bodyFill
      bodyClassName="p-0"
      size="lg"
    >
      <VaccinationForm
        patientId={patientId}
        immunization={immunization}
        formId={formId}
        showActions={false}
        onStateChange={setFormState}
        onSubmit={handleSubmit}
        onCancel={onClose}
      />
    </Modal>
  );
};

export default VaccinationModal;
