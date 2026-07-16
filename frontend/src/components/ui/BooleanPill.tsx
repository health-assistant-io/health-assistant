/**
 * BooleanPill — a compact on/off indicator for boolean flags.
 *
 * Renders a small pill: green with a check when the value is truthy, muted
 * gray with a slash when falsy. Used for catalog flags like
 * `is_telemetry` / `is_custom` / `retired` in the Info tab, and reusable for
 * any boolean display where a colored pill communicates state faster than a
 * "Yes/No" string.
 */
import React from 'react';
import { Check, Minus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

interface BooleanPillProps {
  value: boolean | null | undefined;
  labelOn?: string;
  labelOff?: string;
  /** Override the leading icon (defaults: Check / Minus). */
  icon?: LucideIcon;
  className?: string;
}

export const BooleanPill: React.FC<BooleanPillProps> = ({
  value,
  labelOn = 'Yes',
  labelOff = 'No',
  icon,
  className = '',
}) => {
  const on = value === true;
  const Icon: LucideIcon = icon ?? (on ? Check : Minus);
  const cls = on
    ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
    : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400';
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls} ${className}`}
    >
      <Icon className="w-3 h-3" aria-hidden />
      {on ? labelOn : labelOff}
    </span>
  );
};

export default BooleanPill;
