import { useEffect } from 'react';

/**
 * Adds Escape-to-close + body scroll-lock to any bespoke modal/drawer.
 *
 * Drop-in fix for the ~30 bespoke `fixed inset-0` overlays that predate
 * the shared `Modal.tsx` and lack these behaviours. Call once at the top
 * of the component body:
 *
 *   useModalA11y(isOpen, onClose);
 *
 * For ARIA roles, add `role="dialog" aria-modal="true"` to the panel
 * element manually (the hook cannot do this automatically).
 */
export function useModalA11y(isOpen: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    document.addEventListener('keydown', handleEscape);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = prev;
    };
  }, [isOpen, onClose]);
}
