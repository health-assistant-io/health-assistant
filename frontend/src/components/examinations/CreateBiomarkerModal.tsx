import React from 'react';
import biomarkerService from '../../services/biomarkerService';
import { BiomarkerDefinitionForm, BiomarkerDefinitionFormPayload } from './BiomarkerDefinitionForm';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (newBiomarker: any) => void;
  initialName?: string;
}

/**
 * Thin portal wrapper that hosts the headless {@link BiomarkerDefinitionForm}.
 * The form itself is shared with the AI HITL handler — this modal only provides
 * the overlay + commit wiring for the manual catalog-create flow.
 */
export const CreateBiomarkerModal: React.FC<Props> = ({ isOpen, onClose, onSuccess, initialName = '' }) => {
  if (!isOpen) return null;

  const handleSubmit = async (payload: BiomarkerDefinitionFormPayload) => {
    try {
      const result = await biomarkerService.createBiomarker(payload);
      onSuccess(result);
      onClose();
    } catch (err) {
      console.error('Failed to create biomarker', err);
      // Preserve the legacy behavior: surface a blocking alert. The slug collision
      // is the most common cause.
      alert('Failed to create biomarker definition. Slug might already exist.');
      throw err; // re-throw so the form keeps its loading state reset + caller sees it
    }
  };

  return (
    <div className="fixed inset-0 z-[1100] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh]">
        <BiomarkerDefinitionForm
          prefill={initialName ? { name: initialName } : undefined}
          onSubmit={handleSubmit}
          onCancel={onClose}
        />
      </div>
    </div>
  );
};

