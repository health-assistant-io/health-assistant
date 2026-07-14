/**
 * RepeatableItems<T> — a generic, render-prop driven list editor.
 *
 * The same UI shell (add/remove/move/clear, empty state, item wrapper,
 * per-item index badge) is reused across forms that need a repeating set of
 * values: medication "Scheduled Times" (T = string HH:MM), vaccine
 * "Dose Schedule intervals" (T = string), biomarker reference ranges, etc.
 *
 * The parent owns the array and decides:
 *   - how to create a new blank item (`createItem`)
 *   - how to render an item (`renderItem(value, patch, index)`)
 *
 * The component handles the rest — state plumbing, focus management on add,
 * remove, move up/down, and a polished, modern look matching the rest of the
 * app (rounded cards, hover affordances, dark-mode parity).
 *
 * Example:
 *   <RepeatableItems
 *     items={timing.time_of_day ?? []}
 *     onChange={(next) => setTiming({ ...timing, time_of_day: next })}
 *     createItem={() => '09:00'}
 *     addItemLabel={t('medications.modal.add_time')}
 *     renderItem={(val, patch, i) => (
 *       <TimePicker value={val} onChange={patch} />
 *     )}
 *   />
 */
import React, { useRef, useEffect, useCallback } from 'react';
import { Plus, X, ChevronUp, ChevronDown, GripVertical } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export interface RepeatableItemsProps<T> {
  /** The list of items. Parent owns it. */
  items: T[];
  /** Push a fully replaced list back into the parent. */
  onChange: (items: T[]) => void;
  /** Build a fresh blank item — called when "Add" is clicked. */
  createItem: () => T;
  /**
   * Render one item. The `patch` callback replaces just this slot's value
   * in the parent array (no manual index juggling).
   * Optional `disabled` mirrors the parent prop.
   */
  renderItem: (value: T, patch: (next: T) => void, index: number, disabled?: boolean) => React.ReactNode;
  /** Label for the "Add" button. Falls back to i18n key `common.add_item`. */
  addItemLabel?: string;
  /** Optional heading shown above the list. */
  title?: string;
  /** Optional hint under the title. */
  hint?: string;
  /** Disable every row + the add button (read-only mode). */
  disabled?: boolean;
  /** Hide the reorder up/down controls (default: shown). */
  hideReorder?: boolean;
  /** Hide the per-item remove button (default: shown). */
  hideRemove?: boolean;
  /** Cap the number of items. Once reached, the add button hides. */
  maxItems?: number;
  /** Show a single-line list (rows are tighter). Default false. */
  compact?: boolean;
  /** Extra classes on the outer wrapper. */
  className?: string;
  /** Optional empty-state message when `items.length === 0`. */
  emptyMessage?: string;
  /** Show a small index badge (1, 2, …) on each row. Default true. */
  showIndex?: boolean;
}

export function RepeatableItems<T>({
  items,
  onChange,
  createItem,
  renderItem,
  addItemLabel,
  title,
  hint,
  disabled = false,
  hideReorder = false,
  hideRemove = false,
  maxItems,
  compact = false,
  className = '',
  emptyMessage,
  showIndex = true,
}: RepeatableItemsProps<T>) {
  const { t } = useTranslation();
  const lastIndex = useRef<number>(-1);

  const add = useCallback(() => {
    if (disabled) return;
    if (maxItems !== undefined && items.length >= maxItems) return;
    onChange([...items, createItem()]);
    lastIndex.current = items.length; // mark the new slot for focus ring
  }, [disabled, maxItems, items, onChange, createItem]);

  const patchAt = useCallback(
    (index: number, next: T) => {
      if (disabled) return;
      const copy = items.slice();
      copy[index] = next;
      onChange(copy);
    },
    [disabled, items, onChange],
  );

  const removeAt = useCallback(
    (index: number) => {
      if (disabled) return;
      onChange(items.filter((_, i) => i !== index));
    },
    [disabled, items, onChange],
  );

  const move = useCallback(
    (from: number, to: number) => {
      if (disabled) return;
      if (to < 0 || to >= items.length) return;
      const copy = items.slice();
      const [it] = copy.splice(from, 1);
      copy.splice(to, 0, it);
      onChange(copy);
    },
    [disabled, items, onChange],
  );

  const clearAll = useCallback(() => {
    if (disabled) return;
    onChange([]);
  }, [disabled, onChange]);

  const atMax = maxItems !== undefined && items.length >= maxItems;

  return (
    <div className={`space-y-2 ${className}`}>
      {(title || hint) && (
        <div className="flex items-start justify-between gap-2">
          <div>
            {title && (
              <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest px-1">
                {title}
              </p>
            )}
            {hint && <p className="text-[11px] text-gray-400 px-1 mt-0.5">{hint}</p>}
          </div>
          {items.length > 0 && !disabled && (
            <button
              type="button"
              onClick={clearAll}
              className="text-[10px] font-bold text-gray-400 hover:text-red-500 uppercase tracking-widest shrink-0"
            >
              {t('common.clear_all', 'Clear all')}
            </button>
          )}
        </div>
      )}

      {items.length === 0 && emptyMessage ? (
        <p className="text-xs text-gray-400 italic px-1 py-2">{emptyMessage}</p>
      ) : (
        <div className="space-y-2">
          {items.map((item, i) => (
            <RepeatableRow
              key={i}
              index={i}
              total={items.length}
              compact={compact}
              showIndex={showIndex}
              canReorder={!hideReorder && !disabled && items.length > 1}
              canRemove={!hideRemove && !disabled}
              onUp={() => move(i, i - 1)}
              onDown={() => move(i, i + 1)}
              onRemove={() => removeAt(i)}
            >
              {renderItem(item, (next) => patchAt(i, next), i, disabled)}
            </RepeatableRow>
          ))}
        </div>
      )}

      {!atMax && !disabled && (
        <button
          type="button"
          onClick={add}
          className={`
            w-full flex items-center justify-center gap-2 text-xs font-bold
            transition-all border border-dashed
            ${compact ? 'py-2' : 'py-2.5'}
            border-gray-200 dark:border-dark-border text-gray-400 hover:text-blue-600 dark:hover:text-blue-400
            hover:border-blue-300 dark:hover:border-blue-700 hover:bg-blue-50/50 dark:hover:bg-blue-900/10 rounded-xl
          `}
        >
          <Plus className="w-3.5 h-3.5" />
          {addItemLabel || t('common.add_item', 'Add item')}
        </button>
      )}

      {atMax && maxItems !== undefined && (
        <p className="text-[10px] text-gray-400 text-center italic">
          {t('common.max_items_reached', { defaultValue: 'Maximum of {{n}} items.', n: maxItems })}
        </p>
      )}
    </div>
  );
}

/* --------------------------------- row ----------------------------------- */

interface RowProps {
  index: number;
  total: number;
  compact: boolean;
  showIndex: boolean;
  canReorder: boolean;
  canRemove: boolean;
  onUp: () => void;
  onDown: () => void;
  onRemove: () => void;
  children: React.ReactNode;
}

const RepeatableRow: React.FC<RowProps> = ({
  index,
  total,
  compact,
  showIndex,
  canReorder,
  canRemove,
  onUp,
  onDown,
  onRemove,
  children,
}) => {
  // Briefly flash a highlight ring when a slot is freshly added.
  const rowRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (index === total - 1 && rowRef.current) {
      // Avoid auto-scroll jank inside small modals; just focus-ring once.
      rowRef.current.classList.add('ring-2', 'ring-blue-500/40');
      const id = window.setTimeout(() => {
        rowRef.current?.classList.remove('ring-2', 'ring-blue-500/40');
      }, 600);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [index, total]);

  return (
    <div
      ref={rowRef}
      className={`
        group flex items-stretch gap-2 rounded-xl transition-all
        ${compact ? 'py-1' : 'py-1.5'}
        bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border
        hover:border-blue-200 dark:hover:border-blue-800
      `}
    >
      {showIndex && (
        <div className="flex flex-col items-center justify-center w-7 shrink-0">
          {canReorder ? (
            <GripVertical className="w-3.5 h-3.5 text-gray-300 dark:text-gray-600 group-hover:text-gray-400" />
          ) : (
            <span className="text-[10px] font-bold text-gray-400 tabular-nums">{index + 1}</span>
          )}
        </div>
      )}

      <div className="flex-1 min-w-0 py-1">{children}</div>

      {(canReorder || canRemove) && (
        <div className="flex flex-col items-center justify-center gap-0.5 pr-1.5 shrink-0">
          {canReorder && (
            <>
              <button
                type="button"
                onClick={onUp}
                disabled={index === 0}
                aria-label="Move up"
                className="p-1 text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 disabled:opacity-30 disabled:hover:text-gray-300"
              >
                <ChevronUp className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={onDown}
                disabled={index === total - 1}
                aria-label="Move down"
                className="p-1 text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 disabled:opacity-30 disabled:hover:text-gray-300"
              >
                <ChevronDown className="w-3.5 h-3.5" />
              </button>
            </>
          )}
          {canRemove && (
            <button
              type="button"
              onClick={onRemove}
              aria-label="Remove"
              className="p-1 text-gray-300 hover:text-red-500 dark:hover:text-red-400"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      )}
    </div>
  );
};
