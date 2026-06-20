import React from 'react';
import { AddBiomarkerForm } from './AddBiomarkerForm';
import { createObservation } from '../../services/fhirService';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  examinationId: string;
  onSuccess: () => void;
}

/**
 * Thin portal wrapper around the headless AddBiomarkerForm. The form logic
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
  if (!isOpen) return null;

  const handleSubmit = async (observation: Parameters<React.ComponentProps<typeof AddBiomarkerForm>['onSubmit']>[0]) => {
    await createObservation(observation as any);
    onSuccess();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh]">
        <AddBiomarkerForm
          patientId={patientId}
          examinationId={examinationId}
          onSubmit={handleSubmit}
          onCancel={onClose}
          showHeader
          showActions
        />
      </div>
    </div>
  );
};
