import React from 'react';
import { useUIStore } from '../../store/slices/uiSlice';
import {
  ConfirmationModal as LibraryConfirmationModal,
} from '@neuronection/assistant-ui';

/**
 * App-side glue: the confirmation state lives in the ui store; presentation
 * comes from the library `ConfirmationModal` (ADR-006 — the store
 * subscription can't live in the library).
 */
export const ConfirmationModal: React.FC = () => {
  const { confirmationModal, hideConfirmation } = useUIStore();
  const open = !!confirmationModal;

  const handleConfirm = async () => {
    if (!confirmationModal) return;
    await confirmationModal.onConfirm();
    hideConfirmation();
  };

  return (
    <LibraryConfirmationModal
      open={open}
      onOpenChange={(next) => {
        if (!next) hideConfirmation();
      }}
      title={confirmationModal?.title ?? ''}
      description={confirmationModal?.message}
      confirmLabel={confirmationModal?.confirmLabel}
      cancelLabel={confirmationModal?.cancelLabel}
      destructive={confirmationModal?.confirmVariant === 'danger'}
      onConfirm={handleConfirm}
    />
  );
};
