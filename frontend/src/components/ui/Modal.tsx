import React, { useEffect, useRef, useCallback } from 'react';
import { X } from 'lucide-react';
import { Portal } from './Portal';

type ModalSize = 'sm' | 'md' | 'lg' | 'xl';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  className?: string;
  /** Desktop max-width. Mobile is always full-screen. */
  size?: ModalSize;
  /** Optional icon node rendered before the title. */
  headerIcon?: React.ReactNode;
  /** Extra elements (buttons) in the header, left of the close button. */
  headerActions?: React.ReactNode;
  /** Sticky footer (e.g. form actions). Pinned to the bottom of the panel. */
  footer?: React.ReactNode;
  /** When true, no header bar — the close button floats. */
  hideHeader?: boolean;
  /** Override body padding (default `p-6`). */
  bodyClassName?: string;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea, input:not([disabled]), select, [tabindex]:not([tabindex="-1"])';

const SIZE_CLASSES: Record<ModalSize, string> = {
  sm: 'sm:max-w-md',
  md: 'sm:max-w-2xl',
  lg: 'sm:max-w-4xl',
  xl: 'sm:max-w-6xl',
};

/**
 * Responsive modal — **full-screen on mobile**, centered card on desktop.
 *
 * Behaviour:
 * - Mobile (<640px): panel covers the entire viewport (safe-area aware).
 *   A header with title + close is always visible while the body scrolls.
 * - Desktop (>=640px): standard centered card with overlay, rounded corners.
 *
 * Accessibility:
 * - `role="dialog"` + `aria-modal="true"`
 * - Escape-to-close, Tab focus trap, focus restore on unmount
 * - Body scroll lock while open
 *
 * Backward-compatible: `title` + `children` is all that's required.
 */
export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  className = '',
  size = 'md',
  headerIcon,
  headerActions,
  footer,
  hideHeader = false,
  bodyClassName = 'p-6',
}) => {
  const overlayRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  // Keep the latest ``onClose`` without re-running the focus effect on every
  // render. Parents commonly pass an inline arrow (``() => setOpen(false)``),
  // so including ``onClose`` in the effect deps would tear the effect down +
  // re-run on each keystroke — its cleanup restores focus to the pre-modal
  // element, yanking the caret out of the input mid-typing.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const handleTab = useCallback((e: KeyboardEvent) => {
    if (e.key !== 'Tab' || !panelRef.current) return;
    const nodes = panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    if (nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];

    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current();
    };

    previousFocus.current = document.activeElement as HTMLElement;
    document.addEventListener('keydown', handleEscape);
    document.addEventListener('keydown', handleTab);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    requestAnimationFrame(() => {
      const target =
        panelRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ||
        panelRef.current;
      target?.focus();
    });

    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.removeEventListener('keydown', handleTab);
      document.body.style.overflow = prevOverflow;
      previousFocus.current?.focus();
    };
    // Only re-run on open/close transition — see the onCloseRef comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  if (!isOpen) return null;

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
  };

  return (
    <Portal>
      {/* Overlay — transparent on mobile (panel is full-screen), dark blur on desktop */}
      <div
        ref={overlayRef}
        onClick={handleOverlayClick}
        className="fixed inset-0 z-modal flex flex-col sm items-center sm:justify-center sm:bg-black/60 sm:backdrop-blur-sm sm:p-6 animate-in fade-in duration-200"
      >
        {/* Panel — full-screen on mobile, card on desktop */}
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          tabIndex={-1}
          className={`
            bg-white dark:bg-dark-surface flex flex-col overflow-hidden focus:outline-none
            w-full h-full safe-top safe-bottom
            sm:w-auto sm:h-auto ${SIZE_CLASSES[size]} sm:max-h-[90vh]
            sm:rounded-2xl shadow-xl
            animate-in fade-in slide-in-from-bottom-4 sm:zoom-in-95 duration-200
            ${className}
          `}
        >
          {/* Header (skip when hideHeader) */}
          {!hideHeader && (
            <div className="flex items-center justify-between gap-3 px-5 sm:px-6 py-4 border-b border-gray-100 dark:border-dark-border shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                {headerIcon && <div className="shrink-0">{headerIcon}</div>}
                {title && (
                  <h2 className="text-base sm:text-lg font-bold text-gray-900 dark:text-dark-text truncate">
                    {title}
                  </h2>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {headerActions}
                <button
                  onClick={onClose}
                  aria-label="Close"
                  className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-dark-bg transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
          )}

          {/* Scrollable body */}
          <div className={`overflow-y-auto custom-scrollbar flex-1 ${bodyClassName}`}>
            {children}
          </div>

          {/* Sticky footer */}
          {footer && (
            <div className="px-5 sm:px-6 py-4 border-t border-gray-100 dark:border-dark-border shrink-0">
              {footer}
            </div>
          )}
        </div>
      </div>
    </Portal>
  );
};

export default Modal;
