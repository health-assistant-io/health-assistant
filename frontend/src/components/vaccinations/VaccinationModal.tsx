/**
 * VaccinationModal — Modal host for {@link VaccinationForm}.
 *
 * Owns the API writes so the form stays a pure controlled component:
 *  - add / edit a patient immunization (`/vaccines/*`)
 *  - optionally create a vaccine catalog entry inline (the "define new" flow)
 *  - persist graph links on the catalog entry via `createLinksFor`
 *    (`srcType="immunization"`) after the primary record is committed
 *
 * Mirrors `MedicationModal`. Links only persist when a `catalog_id` is known
 * (picked or just-created) — links can't attach to a denormalized record with
 * no catalog entry.
 */
import React from 'react';
import { Modal } from '../ui/Modal';
import {
  VaccinationForm,
  type VaccinationFormPayload,
} from './VaccinationForm';
import type { PatientImmunization } from '../../types/vaccine';
import { addPatientImmunization, updatePatientImmunization } from '../../services/vaccineService';
import { createCatalogItem } from '../../services/catalogService';
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
  if (!isOpen) return null;

  const handleSubmit = async (payload: VaccinationFormPayload) => {
    let catalogId = payload.vaccine_code.catalog_id ?? null;
    let drugName = payload.vaccine_code.text;

    // Inline "define new vaccine" → create a minimal catalog entry. Disease
    // association is handled in the catalog via LinksSection (PREVENTS edges),
    // not as free text here.
    if (payload.is_new_catalog_entry) {
      const created = await createCatalogItem('vaccine', {
        name: payload.vaccine_code.text,
      });
      catalogId = String((created as { id?: string }).id ?? '');
      drugName = String((created as { name?: string }).name ?? drugName);
    }

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
        : { text: drugName, catalog_id: catalogId ?? undefined },
    };

    if (immunization) {
      await updatePatientImmunization(immunization.id, commit);
    } else {
      await addPatientImmunization(patientId, {
        vaccine_catalog_id: catalogId,
        ...commit,
        vaccine_code: {
          text: drugName,
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
      isOpen={isOpen}
      onClose={onClose}
      title=""
      hideHeader
      footer={undefined}
      bodyClassName="p-0"
      size="lg"
    >
      <VaccinationForm
        patientId={patientId}
        immunization={immunization}
        onSubmit={handleSubmit}
        onCancel={onClose}
      />
    </Modal>
  );
};

export default VaccinationModal;
