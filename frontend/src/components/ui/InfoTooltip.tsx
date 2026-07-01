import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Info, X } from 'lucide-react';

interface InfoTooltipProps {
  content: React.ReactNode;
  /** Optional heading rendered at the top of the popover (especially useful in `click` mode). */
  title?: string;
  icon?: React.ReactNode;
  className?: string;
  position?: 'top' | 'bottom' | 'left' | 'right';
  /** `hover` (default — original behavior, tiny tooltip) or `click` (rich popover with dismissal, Portal-rendered to escape card overflow clipping). */
  trigger?: 'hover' | 'click';
  /** Accessible label for the trigger icon. Defaults to "Information". */
  ariaLabel?: string;
}

const POSITION_CLASSES: Record<string, string> = {
  top: 'bottom-full mb-2 left-1/2 -translate-x-1/2',
  bottom: 'top-full mt-2 left-1/2 -translate-x-1/2',
  left: 'right-full mr-2 top-1/2 -translate-y-1/2',
  right: 'left-full ml-2 top-1/2 -translate-y-1/2',
};

export const InfoTooltip: React.FC<InfoTooltipProps> = ({
  content,
  title,
  icon = <Info className="w-4 h-4" />,
  className = '',
  position = 'top',
  trigger = 'hover',
  ariaLabel = 'Information',
}) => {
  const [isVisible, setIsVisible] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState<{ top: number; left: number; placement: 'above' | 'below' } | null>(null);

  // ---------- Click mode: positioning + dismissal ----------
  useEffect(() => {
    if (trigger !== 'click' || !isVisible) return;

    const measure = () => {
      if (!triggerRef.current) return;
      const rect = triggerRef.current.getBoundingClientRect();
      const POPOVER_HEIGHT_ESTIMATE = 160; // generous estimate for collision check
      const placeAbove = rect.top > POPOVER_HEIGHT_ESTIMATE + 16;
      setCoords({
        top: placeAbove ? rect.top - 8 : rect.bottom + 8,
        left: rect.left + rect.width / 2,
        placement: placeAbove ? 'above' : 'below',
      });
    };

    measure();

    const handleClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        triggerRef.current?.contains(target) ||
        popoverRef.current?.contains(target)
      ) {
        return;
      }
      setIsVisible(false);
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsVisible(false);
    };

    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [trigger, isVisible]);

  // ---------- Click trigger ----------
  if (trigger === 'click') {
    return (
      <>
        <button
          type="button"
          ref={triggerRef}
          onClick={(e) => { e.stopPropagation(); setIsVisible(prev => !prev); }}
          aria-label={ariaLabel}
          aria-expanded={isVisible}
          aria-haspopup="dialog"
          className={`inline-flex items-center justify-center text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg p-1 transition-colors cursor-pointer ${className}`}
        >
          {icon}
        </button>

        {isVisible && coords &&
          createPortal(
            <div
              ref={popoverRef}
              role="dialog"
              aria-label={title || ariaLabel}
              style={{
                position: 'fixed',
                top: coords.top,
                left: coords.left,
                transform: coords.placement === 'above' ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
              }}
              className="z-[300] w-72 max-w-[calc(100vw-2rem)] p-4 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border text-gray-700 dark:text-dark-text text-xs rounded-2xl shadow-2xl ring-1 ring-black/5 dark:ring-white/5 animate-in fade-in zoom-in-95 duration-150"
            >
              <button
                type="button"
                onClick={() => setIsVisible(false)}
                aria-label="Close"
                className="absolute top-2 right-2 p-1 text-gray-300 hover:text-gray-600 dark:text-dark-muted dark:hover:text-dark-text rounded-md hover:bg-gray-50 dark:hover:bg-dark-bg transition-colors"
              >
                <X className="w-3 h-3" />
              </button>
              {title && (
                <h3 className="font-black text-sm text-gray-900 dark:text-dark-text mb-1.5 pr-4 uppercase tracking-wide">
                  {title}
                </h3>
              )}
              <div className="leading-relaxed text-gray-600 dark:text-dark-muted">{content}</div>
            </div>,
            document.body
          )}
      </>
    );
  }

  // ---------- Hover trigger (original behavior, backward-compatible) ----------
  return (
    <div
      className={`relative inline-flex ${className}`}
      onMouseEnter={() => setIsVisible(true)}
      onMouseLeave={() => setIsVisible(false)}
      onFocus={() => setIsVisible(true)}
      onBlur={() => setIsVisible(false)}
      tabIndex={0}
      role="button"
      aria-label={ariaLabel}
    >
      <div className="cursor-help text-gray-400 hover:text-blue-500 transition-colors">
        {icon}
      </div>

      {isVisible && (
        <div
          className={`absolute z-[100] w-64 p-3 bg-gray-900 dark:bg-gray-800 text-white text-xs font-medium rounded-xl shadow-xl animate-in fade-in zoom-in-95 duration-200 pointer-events-none ${POSITION_CLASSES[position]}`}
        >
          {title && <h3 className="font-bold mb-1 text-white">{title}</h3>}
          {content}
        </div>
      )}
    </div>
  );
};

export default InfoTooltip;
