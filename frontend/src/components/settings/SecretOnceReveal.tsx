import type { ReactNode } from 'react';
import { SecretOnceBlock } from '@neuronection/assistant-ui/secret-once-block';
import { Modal } from '../ui/Modal';

export interface SecretOnceRevealProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  warning: string;
  secret: string;
  testId?: string;
  copyLabel?: string;
  copiedLabel?: string;
  className?: string;
  children?: ReactNode;
  footer?: ReactNode;
}

/**
 * Shown-once secret dialog: the app `Modal` chrome plus the library
 * `SecretOnceBlock` body (value, warning, copy). Minting, storage and any
 * surrounding context (e.g. the OAuth client id) stay with the caller.
 */
export function SecretOnceReveal({
  open,
  onClose,
  title,
  description,
  warning,
  secret,
  testId,
  copyLabel,
  copiedLabel,
  className,
  children,
  footer,
}: SecretOnceRevealProps) {
  return (
    <Modal
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={title}
      description={description}
      className={className}
      footer={footer}
    >
      <div className="space-y-4">
        {children}
        <SecretOnceBlock
          secret={secret}
          warning={warning}
          testId={testId}
          copyLabel={copyLabel}
          copiedLabel={copiedLabel}
        />
      </div>
    </Modal>
  );
}
