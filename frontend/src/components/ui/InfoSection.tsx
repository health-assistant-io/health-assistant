/**
 * InfoSection — a titled, optionally-collapsible group used to give the
 * catalog Info tab (and future detail panels) visual hierarchy.
 *
 * Renders a tiny uppercase header with an optional leading lucide icon and a
 * thin divider, then its children. When `collapsible`, the header becomes a
 * button that toggles the body with a rotating chevron, wired for keyboard
 * (Enter/Space) and screen readers (`aria-expanded` / `aria-controls`).
 *
 * Sections are grouped via label + hairline rather than nested boxes, which
 * keeps the layout compact (no card-within-card padding inflation).
 */
import React, { useId, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

interface InfoSectionProps {
  title: string;
  icon?: LucideIcon;
  collapsible?: boolean;
  defaultOpen?: boolean;
  className?: string;
  /** Body wrapper className (e.g. to tighten spacing). */
  bodyClassName?: string;
  children: React.ReactNode;
}

export const InfoSection: React.FC<InfoSectionProps> = ({
  title,
  icon: Icon,
  collapsible = false,
  defaultOpen = true,
  className = '',
  bodyClassName = '',
  children,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();

  const header = (
    <>
      {Icon && <Icon className="w-3.5 h-3.5 text-gray-400" aria-hidden />}
      <span>{title}</span>
      {collapsible && (
        <ChevronDown
          className={`w-3.5 h-3.5 ml-auto transition-transform ${open ? '' : '-rotate-90'}`}
          aria-hidden
        />
      )}
    </>
  );

  return (
    <section className={`space-y-1.5 ${className}`}>
      {collapsible ? (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="flex items-center gap-1.5 w-full text-left text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
        >
          {header}
        </button>
      ) : (
        <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500 py-1 border-b border-gray-100 dark:border-gray-700/60">
          {header}
        </div>
      )}
      {(!collapsible || open) && (
        <div id={bodyId} className={bodyClassName}>
          {children}
        </div>
      )}
    </section>
  );
};

export default InfoSection;
