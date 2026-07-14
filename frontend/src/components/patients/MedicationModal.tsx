import React from 'react';
import {
  MedicationRecord,
  addCustomMedication,
  addPatientMedication,
  updatePatientMedication
} from '../../services/medicationService';
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
      const newEntry = await addCustomMedication({
        name: payload.code.text,
        indications: payload.indications,
        side_effects: payload.side_effects || [],
        dosage_info: payload.dosage || undefined
      });
      catalogId = newEntry.id;
      drugName = newEntry.name;
    }

    const commitPayload: any = {
      status: payload.status,
      dosage: payload.dosage,
      frequency: payload.frequency,
      start_date: payload.start_date,
      end_date: payload.end_date,
      reason: payload.reason,
      note: payload.note,
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
