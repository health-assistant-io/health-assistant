/**
 * Generic instance browser — presentational scrollable list/grid of patient-
 * scoped records, projected into the uniform {@link InstanceRow} shape by each
 * entity's adapter. The instance counterpart of `CatalogBrowser`.
 *
 * Deliberately "dumb": the parent (the browse modal, or a future list page)
 * owns loading, selection, filtering, and pagination. This component only:
 *   - sorts rows (label / date / type),
 *   - highlights the active search term,
 *   - renders status + type + extra badges (clickable to drive parent filters),
 *   - offers a picker-mode Add/Added toggle per row,
 *   - keyboard navigation (↑/↓/Enter/Esc),
 *   - incremental "Load more".
 *
 * Because it operates on `InstanceRow[]` (not the raw entity), it contains
 * zero entity-specific field access — every visual is derived from the row.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  SearchX,
  Inbox,
  ChevronDown,
  Loader2,
  Plus,
  Check,
  ExternalLink,
  Calendar,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow, isValid } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import { DynamicIcon } from '../ui/DynamicIcon';
import type { InstanceRow, InstanceType } from './types';

type SortKey = 'label' | 'date' | 'type';
type SortDir = 'asc' | 'desc';

/** Wraps case-insensitive matches of `term` inside `text` with <mark>. */
const Highlight: React.FC<{ text: string; term?: string }> = ({ text, term }) => {
  const q = term?.trim();
  if (!q) return <>{text}</>;
  const lower = text.toLowerCase();
  const ql = q.toLowerCase();
  if (!ql || !lower.includes(ql)) return <>{text}</>;
  const out: React.ReactNode[] = [];
  let i = 0;
  let idx = lower.indexOf(ql);
  let key = 0;
  while (idx !== -1) {
    if (idx > i) out.push(text.slice(i, idx));
    out.push(
      <mark
        key={key++}
        className="bg-yellow-200 dark:bg-yellow-500/40 text-inherit rounded px-0.5"
      >
        {text.slice(idx, idx + q.length)}
      </mark>,
    );
    i = idx + q.length;
    idx = lower.indexOf(ql, i);
  }
  if (i < text.length) out.push(text.slice(i));
  return <>{out}</>;
};

export interface InstanceBrowserProps {
  /** Rows to render (already fetched; this component does not fetch). */
  rows: InstanceRow[];
  /** True while the parent is (re)loading the first page. */
  loading: boolean;
  /** Total in the (filtered) collection — drives the "N of M" count. */
  total: number;
  /** Active page-search term (highlighted in label/subtitle). */
  searchTerm?: string;

  /** Sort controls are multi-type aware: 'type' sort is shown when >1 type. */
  showTypeSort?: boolean;

  /** Whether any search/filter is active (drives the empty-state copy). */
  hasActiveFilters?: boolean;
  onClearFilters?: () => void;

  /** Selection (workspace use): the id of the currently-opened row. */
  selectedId?: string;
  onSelectRow?: (row: InstanceRow) => void;

  /** Detail link resolver (renders an "Open in …" affordance per row). */
  detailRoute?: (row: InstanceRow) => string | null;

  /** Picker mode. When `onTogglePick` is supplied each row renders Add/Added. */
  pickedIds?: string[];
  onTogglePick?: (row: InstanceRow) => void;

  /** Click a type chip to filter to that type (multi-type browse only). */
  activeType?: InstanceType | null;
  onTypeClick?: (type: InstanceType) => void;

  /** Incremental pagination. */
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
}

export const InstanceBrowser: React.FC<InstanceBrowserProps> = ({
  rows,
  loading,
  total,
  searchTerm,
  showTypeSort = false,
  hasActiveFilters,
  onClearFilters,
  selectedId,
  onSelectRow,
  detailRoute,
  pickedIds,
  onTogglePick,
  activeType,
  onTypeClick,
  hasMore,
  loadingMore,
  onLoadMore,
}) => {
  const { t, i18n } = useTranslation();
  const locale: Locale = i18n.language.startsWith('el') ? el : enUS;
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const [sortBy, setSortBy] = useState<SortKey>('date');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const toggleSort = (key: SortKey) => {
    if (sortBy === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortBy(key);
      setSortDir(key === 'date' ? 'desc' : 'asc');
    }
  };

  const sortedRows = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (sortBy === 'date') {
        const ta = a.date ? new Date(a.date).getTime() : 0;
        const tb = b.date ? new Date(b.date).getTime() : 0;
        return (ta - tb) * dir;
      }
      if (sortBy === 'type') {
        return a.type.localeCompare(b.type) * dir || a.label.localeCompare(b.label) * dir;
      }
      return a.label.localeCompare(b.label) * dir;
    });
  }, [rows, sortBy, sortDir]);

  /** Keyboard navigation: ↑/↓ moves selection, Enter picks (if picker), Esc clears. */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (sortedRows.length === 0) return;
    const curIdx = selectedId
      ? sortedRows.findIndex((r) => r.id === selectedId)
      : -1;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = sortedRows[Math.min(curIdx + 1, sortedRows.length - 1)];
      if (next) onSelectRow?.(next);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = sortedRows[Math.max(curIdx - 1, 0)];
      if (prev) onSelectRow?.(prev);
    } else if (e.key === 'Escape' && selectedId) {
      e.preventDefault();
      const empty = sortedRows[0];
      if (empty) onSelectRow?.(empty); // clear highlight to first
    } else if (e.key === 'Enter' && curIdx >= 0) {
      const cur = sortedRows[curIdx];
      if (cur && onTogglePick) onTogglePick(cur);
    }
  };

  // Keep the selected row in view (nearest, so click selection isn't disturbed).
  useEffect(() => {
    if (!selectedId || !scrollRef.current) return;
    const node = scrollRef.current.querySelector(
      `[data-row-id="${CSS.escape(selectedId)}"]`,
    );
    node?.scrollIntoView?.({ block: 'nearest' });
  }, [selectedId]);

  const sortKeys: SortKey[] = showTypeSort
    ? ['date', 'label', 'type']
    : ['date', 'label'];

  return (
    <div className="flex flex-col h-full min-h-0 gap-2">
      {/* Sort + count header */}
      <div className="shrink-0 flex items-center justify-between gap-2 px-1">
        <div className="flex items-center gap-1">
          {sortKeys.map((key) => {
            const activeSort = sortBy === key;
            const Icon = !activeSort
              ? ArrowUpDown
              : sortDir === 'asc'
                ? ArrowUp
                : ArrowDown;
            const fallback =
              key === 'date' ? 'Date' : key === 'label' ? 'Name' : 'Type';
            return (
              <button
                key={key}
                type="button"
                onClick={() => toggleSort(key)}
                className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
                  activeSort
                    ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300'
                    : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                <Icon className="w-3 h-3" />
                {t(`instances.sort_${key}`, fallback)}
              </button>
            );
          })}
        </div>
        <span className="text-[11px] text-gray-400 shrink-0">
          {rows.length === total
            ? `${total} ${t('common.total', 'total')}`
            : t('instances.count_of', {
                count: rows.length,
                total,
                defaultValue: `${rows.length} of ${total}`,
              })}
        </span>
      </div>

      <div
        ref={scrollRef}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        title={t('instances.keyboard_hint', '↑↓ navigate · Enter select · Esc clear')}
        className="flex-1 overflow-y-auto min-h-0 custom-scrollbar outline-none focus:ring-1 focus:ring-blue-200 dark:focus:ring-blue-900 rounded-lg"
      >
        {loading ? (
          <ul className="divide-y divide-gray-100 dark:divide-gray-700 rounded-lg border border-gray-200 dark:border-gray-700">
            {Array.from({ length: 7 }).map((_, i) => (
              <li key={i} className="flex items-center gap-3 px-4 py-3.5">
                <div className="flex-1 space-y-2">
                  <div className="h-3.5 w-2/3 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
                  <div className="h-3 w-1/2 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
                </div>
                <div className="h-3.5 w-12 bg-gray-100 dark:bg-gray-800 rounded-full animate-pulse" />
              </li>
            ))}
          </ul>
        ) : sortedRows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-14 px-4 text-center">
            {hasActiveFilters ? (
              <>
                <SearchX className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
                <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                  {t('instances.no_matches', 'No matches')}
                </p>
                <p className="text-xs text-gray-400 mt-1">
                  {t('instances.no_matches_hint', 'Try adjusting your search or filters.')}
                </p>
                {onClearFilters && (
                  <button
                    type="button"
                    onClick={onClearFilters}
                    className="mt-3 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    {t('common.clear_filters', 'Clear filters')}
                  </button>
                )}
              </>
            ) : (
              <>
                <Inbox className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
                <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                  {t('instances.empty_title', 'No records yet')}
                </p>
                <p className="text-xs text-gray-400 mt-1">
                  {t('instances.empty_hint', 'Records for this patient will appear here.')}
                </p>
              </>
            )}
          </div>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-700 rounded-lg border border-gray-200 dark:border-gray-700">
            {sortedRows.map((row) => {
              const isPicked = !!pickedIds?.includes(row.id);
              const active =
                (selectedId && row.id === selectedId) || isPicked;
              const route = detailRoute?.(row) ?? null;
              const dateObj = row.date ? new Date(row.date) : null;
              const relDate =
                dateObj && isValid(dateObj)
                  ? formatDistanceToNow(dateObj, { addSuffix: true, locale })
                  : null;
              const typeActive = !!activeType && activeType === row.type;

              return (
                <li
                  key={`${row.type}:${row.id}`}
                  data-row-id={row.id}
                  onClick={() => onSelectRow?.(row)}
                  className={`flex items-start gap-3 px-4 py-3.5 ${
                    onSelectRow ? 'cursor-pointer' : ''
                  } ${
                    active
                      ? 'bg-blue-50 dark:bg-blue-900/30 shadow-[inset_3px_0_0_0_#3b82f6]'
                      : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'
                  }`}
                >
                  {/* Icon */}
                  <div className="shrink-0 mt-0.5 text-gray-400 dark:text-gray-500">
                    {row.icon ? (
                      <DynamicIcon icon={row.icon} className="w-4 h-4" />
                    ) : (
                      <Inbox className="w-4 h-4" />
                    )}
                  </div>

                  <div className="flex-1 text-left min-w-0">
                    <span className="font-semibold text-sm block truncate">
                      <Highlight text={row.label} term={searchTerm} />
                    </span>
                    {row.subtitle && (
                      <span className="block text-xs text-gray-500 dark:text-gray-400 line-clamp-2 leading-snug mt-0.5">
                        <Highlight text={row.subtitle} term={searchTerm} />
                      </span>
                    )}
                    <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                      {/* Type chip — clickable in multi-type browse. */}
                      {showTypeSort && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onTypeClick?.(row.type);
                          }}
                          className={`text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5 ${
                            onTypeClick ? 'cursor-pointer ring-1 ring-inset ring-transparent hover:ring-current' : ''
                          } ${
                            typeActive ? 'ring-2 ring-blue-400' : ''
                          } text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20`}
                          title={row.type}
                        >
                          {t(
                            `instances.type_${row.type}`,
                            row.type,
                          )}
                        </button>
                      )}
                      {/* Status badge */}
                      {row.status && (
                        <span
                          className="text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5"
                          style={
                            row.statusColor
                              ? {
                                  backgroundColor: `${row.statusColor}1a`,
                                  color: row.statusColor,
                                }
                              : undefined
                          }
                        >
                          {row.status}
                        </span>
                      )}
                      {/* Extra badges */}
                      {row.badges?.map((b, i) => (
                        <span
                          key={i}
                          className="text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                          style={
                            b.color
                              ? {
                                  backgroundColor: `${b.color}1a`,
                                  color: b.color,
                                }
                              : undefined
                          }
                        >
                          {b.label}
                        </span>
                      ))}
                      {relDate && (
                        <span className="inline-flex items-center gap-0.5 text-[11px] text-gray-400">
                          <Calendar className="w-3 h-3" /> {relDate}
                        </span>
                      )}
                    </div>
                  </div>

                  <div
                    className="flex flex-col items-end gap-1.5 shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {route && (
                      <a
                        href={route}
                        className="p-1.5 text-gray-400 hover:text-blue-500"
                        title={t('instances.open_in_domain', 'Open in domain view')}
                      >
                        <ExternalLink className="w-4 h-4" />
                      </a>
                    )}
                    {onTogglePick && (
                      <button
                        type="button"
                        onClick={() => onTogglePick(row)}
                        className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
                          isPicked
                            ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                            : 'bg-blue-600 text-white hover:bg-blue-700'
                        }`}
                      >
                        {isPicked ? (
                          <>
                            <Check className="w-3 h-3" />{' '}
                            {t('instances.picker_added', 'Added')}
                          </>
                        ) : (
                          <>
                            <Plus className="w-3 h-3" />{' '}
                            {t('common.add', 'Add')}
                          </>
                        )}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {hasMore && !loading && sortedRows.length > 0 && (
          <div className="flex justify-center py-4">
            <button
              type="button"
              onClick={onLoadMore}
              disabled={loadingMore || !onLoadMore}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {loadingMore ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('common.loading', 'Loading…')}
                </>
              ) : (
                <>
                  <ChevronDown className="w-4 h-4" />
                  {t('common.load_more', 'Load more')}
                </>
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
