/**
 * TimeList — modern chip-based editor for a list of `HH:MM` times.
 *
 * Each time renders as a tappable chip with the clock icon + a prominent
 * 12-hour readout + AM/PM badge; hovering reveals a remove (×) button.
 * Clicking the chip opens the full `<TimePickerContent>` (clock face +
 * editable HH:MM + AM/PM) in a popover. An inline "+ Add time" pill appends
 * a new blank slot and pops the picker straight away.
 *
 * This is the recommended way to edit `time_of_day: string[]` style fields
 * (medication schedules, recurring-event times, etc.). For a single time
 * value, use `<TimePicker>` directly.
 */
import React, { useRef, useState, useCallback } from 'react';
import { Clock as ClockIcon, Plus, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Popover } from './Popover';
import { TimePickerContent } from './TimePicker';

export interface TimeListProps {
  /** List of 24-hour `HH:MM` strings. */
  value: string[];
  /** Push the fully replaced list back to the parent. */
  onChange: (next: string[]) => void;
  /** Optional heading rendered above the chips. */
  label?: string;
  /** Optional helper text rendered under the label. */
  hint?: string;
  /** "Add" button label. Falls back to i18n `common.add_time`. */
  addLabel?: string;
  /** Empty-state copy when there are no chips yet. */
  emptyLabel?: string;
  /** Cap the number of chips. */
  maxItems?: number;
  /** Disable every chip + the add button. */
  disabled?: boolean;
  /** Diameter of the picker's clock face (default 220). */
  clockSize?: number;
  className?: string;
}

/* Parse "HH:MM" → { h12, min, period }; returns null when unparseable. */
function readTime(value: string): { h12: number; min: number; period: 'AM' | 'PM' } | null {
  const m = /^(\d{1,2}):(\d{1,2})$/.exec((value || '').trim());
  if (!m) return null;
  const h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  if (Number.isNaN(h) || Number.isNaN(min) || h < 0 || h > 23 || min < 0 || min > 59) return null;
  const period: 'AM' | 'PM' = h < 12 ? 'AM' : 'PM';
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return { h12, min, period };
}

interface ChipProps {
  value: string;
  onChange: (v: string) => void;
  onRemove: () => void;
  clockSize: number;
  disabled?: boolean;
}

const TimeChip: React.FC<ChipProps> = ({ value, onChange, onRemove, clockSize, disabled }) => {
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const parsed = readTime(value);

  // Display fallback: if the value is unparseable, show it raw so the user
  // sees what's stored and can fix it by clicking.
  const main = parsed ? parsed.h12.toString() : '--';
  const minStr = parsed ? parsed.min.toString().padStart(2, '0') : '--';
  const period = parsed ? parsed.period : '';

  return (
    <div className="relative inline-flex">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => !disabled && setIsOpen(true)}
        disabled={disabled}
        className={`
          group inline-flex items-center gap-2 pl-3 pr-2 py-2 rounded-xl border
          transition-all select-none
          ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:-translate-y-0.5'}
          bg-white dark:bg-dark-surface
          border-gray-200 dark:border-dark-border
          hover:border-blue-300 dark:hover:border-blue-700
          hover:shadow-md hover:shadow-blue-500/5
          focus:outline-none focus:ring-2 focus:ring-blue-500/40
        `}
      >
        <span
          className="flex items-center justify-center w-7 h-7 rounded-lg shrink-0
                     bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400"
        >
          <ClockIcon className="w-4 h-4" />
        </span>
        <span className="flex items-baseline gap-1 tabular-nums">
          <span className="text-base font-extrabold text-gray-900 dark:text-dark-text leading-none">
            {main}:{minStr}
          </span>
          {period && (
            <span className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-500">
              {period}
            </span>
          )}
        </span>
        {!disabled && (
          <span
            role="button"
            tabIndex={-1}
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                e.stopPropagation();
                onRemove();
              }
            }}
            aria-label="Remove time"
            className="ml-1 flex items-center justify-center w-5 h-5 rounded-full
                       text-gray-300 hover:text-white hover:bg-red-500
                       dark:text-gray-500 dark:hover:bg-red-500 dark:hover:text-white
                       opacity-0 group-hover:opacity-100 focus:opacity-100
                       transition-all"
          >
            <X className="w-3 h-3" />
          </span>
        )}
      </button>

      <Popover
        isOpen={isOpen && !disabled}
        onClose={() => setIsOpen(false)}
        triggerRef={triggerRef as React.RefObject<HTMLElement>}
        side="bottom"
        align="center"
        sideOffset={6}
        className="w-[300px]"
      >
        <TimePickerContent
          value={value}
          onChange={onChange}
          onDone={() => setIsOpen(false)}
          size={clockSize}
        />
      </Popover>
    </div>
  );
};

export const TimeList: React.FC<TimeListProps> = ({
  value,
  onChange,
  label,
  hint,
  addLabel,
  emptyLabel,
  maxItems,
  disabled = false,
  clockSize = 220,
  className = '',
}) => {
  const { t } = useTranslation();

  const updateAt = useCallback(
    (i: number, v: string) => {
      if (disabled) return;
      const next = value.slice();
      next[i] = v;
      onChange(next);
    },
    [disabled, value, onChange],
  );

  const removeAt = useCallback(
    (i: number) => {
      if (disabled) return;
      onChange(value.filter((_, idx) => idx !== i));
    },
    [disabled, value, onChange],
  );

  const add = useCallback(() => {
    if (disabled) return;
    if (maxItems !== undefined && value.length >= maxItems) return;
    onChange([...value, '09:00']);
  }, [disabled, maxItems, value, onChange]);

  const atMax = maxItems !== undefined && value.length >= maxItems;

  return (
    <div className={`space-y-2 ${className}`}>
      {(label || hint) && (
        <div>
          {label && (
            <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1">
              {label}
            </p>
          )}
          {hint && <p className="text-[11px] text-gray-400 px-1 mt-0.5">{hint}</p>}
        </div>
      )}

      {value.length === 0 && emptyLabel ? (
        <p className="text-xs text-gray-400 italic px-1 py-2">{emptyLabel}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {value.map((time, i) => (
            <TimeChip
              key={`${i}-${time}`}
              value={time}
              onChange={(v) => updateAt(i, v)}
              onRemove={() => removeAt(i)}
              clockSize={clockSize}
              disabled={disabled}
            />
          ))}
        </div>
      )}

      {!atMax && !disabled && (
        <button
          type="button"
          onClick={add}
          className="inline-flex items-center gap-1.5 pl-2.5 pr-3 py-1.5 rounded-xl
                     border border-dashed border-gray-300 dark:border-dark-border
                     text-xs font-bold text-gray-400 hover:text-blue-600 dark:hover:text-blue-400
                     hover:border-blue-300 dark:hover:border-blue-700 hover:bg-blue-50/40 dark:hover:bg-blue-900/10
                     transition-all"
        >
          <Plus className="w-3.5 h-3.5" />
          {addLabel || t('common.add_time', 'Add time')}
        </button>
      )}

      {atMax && maxItems !== undefined && (
        <p className="text-[10px] text-gray-400 italic">
          {t('common.max_items_reached', { defaultValue: 'Maximum of {{n}} items.', n: maxItems })}
        </p>
      )}
    </div>
  );
};
