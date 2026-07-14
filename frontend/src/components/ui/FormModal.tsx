import React from 'react';
import { Loader2 } from 'lucide-react';
import { Modal } from './Modal';

type FormSize = 'sm' | 'md' | 'lg' | 'xl';

interface FormModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  /** Desktop max-width. Mobile is always full-screen. */
  size?: FormSize;
  /** Optional icon node in the header. */
  icon?: React.ReactNode;
  /** Extra header buttons (AI assist, etc.). */
  headerActions?: React.ReactNode;
  /** Called when the primary submit button is clicked. */
  onSubmit?: () => void;
  /** Disable the submit button (e.g. when required fields are empty). */
  submitDisabled?: boolean;
  /** Show a spinner on the submit button + disable all actions. */
  submitting?: boolean;
  submitLabel?: string;
  cancelLabel?: string;
  /** When provided, renders a danger "reject" button on the left (HITL flow). */
  onReject?: () => void;
  rejectLabel?: string;
  /** Hide the default footer entirely (use when the form renders its own). */
  hideFooter?: boolean;
  /** Override body padding. */
  bodyClassName?: string;
  /** Extra class on the panel. */
  className?: string;
}

/**
 * Standardized form modal built on `Modal`.
 *
 * Provides a uniform header / scrollable body / sticky footer triptych
 * with consistent submit + cancel buttons across all entity forms
 * (allergy, medication, clinical event, catalog item, …).
 *
 * On mobile the panel is full-screen with safe-area insets.
 */
export const FormModal: React.FC<FormModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  size = 'md',
  icon,
  headerActions,
  onSubmit,
  submitDisabled = false,
  submitting = false,
  submitLabel = 'Save',
  cancelLabel = 'Cancel',
  onReject,
  rejectLabel = 'Reject',
  hideFooter = false,
  bodyClassName,
  className,
}) => {
  const footer = hideFooter ? undefined : (
    <div className="flex items-center justify-between gap-3">
      {/* Reject (HITL) — left aligned danger button */}
      {onReject && (
        <button
          type="button"
          onClick={onReject}
          disabled={submitting}
          className="px-4 py-2.5 text-sm font-bold text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20 rounded-xl transition-colors disabled:opacity-50 disabled:pointer-events-none"
        >
          {rejectLabel}
        </button>
      )}

      <div className="flex items-center gap-3 ml-auto">
        <button
          type="button"
          onClick={onClose}
          disabled={submitting}
          className="px-5 py-2.5 text-sm font-bold text-gray-500 dark:text-dark-muted hover:text-gray-700 dark:hover:text-gray-300 transition-colors disabled:opacity-50 disabled:pointer-events-none"
        >
          {cancelLabel}
        </button>
        {onSubmit && (
          <button
            type="button"
            onClick={onSubmit}
            disabled={submitDisabled || submitting}
            className="inline-flex items-center gap-2 px-6 py-2.5 text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl shadow-md shadow-blue-500/20 transition-all active:scale-95 disabled:opacity-50 disabled:pointer-events-none disabled:active:scale-100"
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            {submitLabel}
          </button>
        )}
      </div>
    </div>
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      size={size}
      headerIcon={icon}
      headerActions={headerActions}
      footer={footer}
      hideHeader={false}
      bodyClassName={bodyClassName}
      className={className}
    >
      {children}
    </Modal>
  );
};

export default FormModal;
