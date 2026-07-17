import React from 'react';
import { MedicationRecord, addPatientMedication, updatePatientMedication } from '../../services/medicationService';
import { createCatalogItem } from '../../services/catalogService';
import { MedicationForm, MedicationFormPayload } from './MedicationForm';
import { Modal } from '../ui/Modal';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  medication?: MedicationRecord;
  onSuccess: () => void;
}

export const MedicationModal: React.FC<Props> = ({ isOpen, onClose, patientId, medication, onSuccess }) => {
  if (!isOpen) return null;

  const handleSubmit = async (payload: MedicationFormPayload) => {
    let catalogId = payload.code.catalog_id;
    let drugName = payload.code.text;

    if (payload.is_new_catalog_entry) {
      const created = await createCatalogItem('medication', {
        name: payload.code.text,
        indications: payload.indications,
        side_effects: payload.side_effects || [],
        dosage_info: payload.dosage || undefined,
      });
      catalogId = String(created.id);
      drugName = String(created.name ?? created.id);
    }

    const commitPayload: any = {
      status: payload.status,
      dosage: payload.dosage,
      frequency: payload.frequency,
      start_date: payload.start_date,
      end_date: payload.end_date,
      reason: payload.reason,
      note: payload.note,
      examination_id: payload.examination_id ?? undefined,
      code: medication ? medication.code : {
        text: drugName,
        catalog_id: catalogId
      }
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
    <Modal isOpen={isOpen} onClose={onClose} title="" hideHeader footer={undefined} bodyClassName="p-0">
      <MedicationForm
        patientId={patientId}
        medication={medication}
        onSubmit={handleSubmit}
        onCancel={onClose}
      />
    </Modal>
  );
};
