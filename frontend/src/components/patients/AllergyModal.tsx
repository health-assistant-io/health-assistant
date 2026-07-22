import React from 'react';
import {
  AllergyIntolerance,
  addPatientAllergy,
  updatePatientAllergy,
} from '../../services/allergyService';
import { createCatalogItem } from '../../services/catalogService';
import {
  AllergyForm,
  AllergyFormPayload,
} from './AllergyForm';
import {
  createLinksFor,
  selectionsToLinkInputs,
} from '../../services/conceptEdges';
import { Modal } from '../ui/Modal';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  allergy?: AllergyIntolerance;
  onSuccess: () => void;
}

/**
 * Thin modal wrapper around the headless `AllergyForm`. The form owns the
 * draft state; this component handles catalog row creation (when defining a
 * new allergen inline), graph-link persistence, and the patient-instance
 * commit via the canonical REST endpoints.
 */
export const AllergyModal: React.FC<Props> = ({ isOpen, onClose, patientId, allergy, onSuccess }) => {
  if (!isOpen) return null;

  const handleSubmit = async (payload: AllergyFormPayload) => {
    let catalogId = payload.code.catalog_id;
    let allergenName = payload.code.text;

    if (payload.is_new_catalog_entry) {
      const created = await createCatalogItem('allergy', {
        name: payload.code.text,
        category: payload.category,
        description: payload.description,
        typical_reactions: payload.typical_reactions ?? [],
      });
      catalogId = String(created.id);
      allergenName = String(created.name ?? created.id);
    }

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
        : {
            text: allergenName,
            catalog_id: catalogId,
          },
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
    <Modal isOpen={isOpen} onClose={onClose} title="" hideHeader footer={undefined} bodyClassName="p-0">
      <AllergyForm
        patientId={patientId}
        allergy={allergy}
        onSubmit={handleSubmit}
        onCancel={onClose}
      />
    </Modal>
  );
};
