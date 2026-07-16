/**
 * ChipList — a read-only list of semantic pill chips.
 *
 * The display counterpart to {@link ChipInput} (which edits): renders an
 * array of strings as small colored pills. Semantic `variant` gives all
 * chips in the list a consistent meaning (neutral aliases, warning side
 * effects, danger allergies/targets, info taxonomy, success flags).
 *
 * Clickable chips: pass `onItemClick` to render each pill as a `<button>`
 * (keyboard-accessible); omit it for static `<span>` pills. Null/empty
 * entries are filtered. When the list is empty, either a muted `emptyText`
 * placeholder or nothing is rendered (caller's choice).
 */
import React from 'react';
import { ChevronRight } from 'lucide-react';

export type ChipVariant = 'neutral' | 'warning' | 'danger' | 'info' | 'success';

interface ChipListProps {
  items: ReadonlyArray<string | null | undefined>;
  variant?: ChipVariant;
  onItemClick?: (value: string, index: number) => void;
  /** Rendered (muted) when the filtered list is empty. Omit to render nothing. */
  emptyText?: string;
  className?: string;
  /** Show a trailing chevron on clickable chips to signal navigability. */
  showChevron?: boolean;
}

const VARIANT_CLASSES: Record<ChipVariant, string> = {
  neutral:
    'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200',
  warning:
    'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  danger:
    'bg-rose-50 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
  info:
    'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
  success:
    'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
};

/** Exported so other badge-style fields (e.g. EnumBadgeField) reuse the same
 *  semantic color palette without redefining it. */
export const CHIP_VARIANT_CLASSES = VARIANT_CLASSES;

const CLICKABLE_BASE =
  'cursor-pointer transition-transform hover:scale-[1.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500';

export const ChipList: React.FC<ChipListProps> = ({
  items,
  variant = 'neutral',
  onItemClick,
  emptyText,
  className = '',
  showChevron = false,
}) => {
  const filtered = items.filter((v): v is string => !!v && String(v).trim() !== '');

  if (filtered.length === 0) {
    if (!emptyText) return null;
    return <span className="text-gray-400 text-sm">{emptyText}</span>;
  }

  const colorCls = VARIANT_CLASSES[variant];
  const pillCls = `inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${colorCls}`;

  return (
    <ul className={`flex flex-wrap gap-1 ${className}`}>
      {filtered.map((value, index) => {
        const label = String(value);
        if (onItemClick) {
          return (
            <li key={`${label}-${index}`}>
              <button
                type="button"
                onClick={() => onItemClick(value, index)}
                className={`${pillCls} ${CLICKABLE_BASE}`}
              >
                {label}
                {showChevron && (
                  <ChevronRight className="w-3 h-3 opacity-70" aria-hidden />
                )}
              </button>
            </li>
          );
        }
        return (
          <li key={`${label}-${index}`}>
            <span className={pillCls}>{label}</span>
          </li>
        );
      })}
    </ul>
  );
};

export default ChipList;
