import React from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { useModalA11y } from '../../hooks/useModalA11y';
import { Portal } from './Portal';

export const ConfirmationModal: React.FC = () => {
  const { confirmationModal, hideConfirmation } = useUIStore();
  const isOpen = !!confirmationModal;
  useModalA11y(isOpen, hideConfirmation);

  if (!confirmationModal) return null;

  const { 
    title, 
    message, 
    confirmLabel = 'Confirm', 
    cancelLabel = 'Cancel', 
    confirmVariant = 'primary',
    onConfirm 
  } = confirmationModal;

  const handleConfirm = async () => {
    await onConfirm();
    hideConfirmation();
  };

  // Portaled + z-popover so the confirmation floats ABOVE any open Modal
  // (e.g. the catalog edit form). Modal.tsx uses z-modal (1000) and also
  // portals to document.body; without the Portal here the confirmation
  // renders earlier in the DOM (inline in Layout) and is painted under the
  // form modal even at equal z-index. z-popover (1050) is the documented
  // tier for "portalled overlays that must float ABOVE a modal".
  return (
    <Portal>
      <div className="fixed inset-0 z-popover flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
      <div role="dialog" aria-modal="true" className="bg-white dark:bg-dark-surface w-full max-w-md max-h-[90vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
        <div className="p-6 overflow-y-auto">
          <div className="flex items-center justify-between mb-4">
            <div className={`p-2 rounded-lg ${confirmVariant === 'danger' ? 'bg-red-50 text-red-600' : 'bg-blue-50 text-blue-600'}`}>
              <AlertTriangle className="w-6 h-6" />
            </div>
            <button 
              onClick={hideConfirmation}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
              aria-label="Close"
            >
              <X className="w-6 h-6" />
            </button>
          </div>
          
          <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text mb-2">
            {title}
          </h3>
          <p className="text-gray-500 dark:text-dark-muted">
            {message}
          </p>
        </div>
        
        <div className="p-6 bg-gray-50 dark:bg-dark-border/30 flex items-center justify-end space-x-3 shrink-0">
          <button
            onClick={hideConfirmation}
            className="px-4 py-2 text-sm font-medium text-gray-600 dark:text-dark-muted hover:text-gray-800 dark:hover:text-gray-200 transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            onClick={handleConfirm}
            className={`px-6 py-2 text-sm font-bold text-white rounded-xl transition-all shadow-md active:scale-95 ${
              confirmVariant === 'danger' 
                ? 'bg-red-600 hover:bg-red-700 shadow-red-200/50' 
                : 'bg-blue-600 hover:bg-blue-700 shadow-blue-200/50'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
        </div>
      </div>
    </Portal>
  );
};
