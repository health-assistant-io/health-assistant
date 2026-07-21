/**
 * Generic catalog browser (list pane) — presentational scrollable list/grid of
 * items. The workspace owns item-loading, selection, and the edit/audit
 * modals; the consolidated `CatalogToolbar` owns search/view/New. This
 * component just renders the rows. Edit / history / delete + the Relations
 * tab live in the preview pane — the list stays scannable. Clicking a row
 * selects it (sets `?item=`); tags (scope/class) are clickable to drive the
 * workspace filters. The header offers client-side sort + a live count.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, GitBranch, ArrowUpDown, ArrowUp, ArrowDown, SearchX, Inbox, ChevronDown, Loader2, Plus, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import type { CatalogItem } from '../../types/catalog';
import { ScopeBadge } from './ScopeBadge';
import { useAuthStore } from '../../store/slices/authSlice';
import { toPlainText } from '../../utils/textFormat';
import { CLASS_COLOR } from '../../types/anatomy';
import { CONCEPT_KIND_LABELS, KIND_COLORS } from '../../types/concept';

/**
 * Tiny colored chip for the item's primary categorical tag.
 *
 * Renders the taxonomy ``class_concept`` when present (biomarker/anatomy/
 * medication/allergy/vaccine), and falls back to the concept ``primary_kind``
 * for taxonomy concepts — so every catalog type surfaces its most important
 * category on the row. Clicking the badge toggles the matching filter facet.
 */
const ClassBadge: React.FC<{
  item: CatalogItem;
  /** Toggle the filter facet for this tag's value. */
  onClick?: (value: string) => void;
  /** Currently-active filter values (marks this tag active). */
  activeValues?: string[];
}> = ({ item, onClick, activeValues }) => {
  const className = item.class_concept_name as string | undefined;
  const classSlug = item.class_concept_slug as string | undefined;
  // Concepts carry no class_concept — surface their primary_kind instead.
  const kind = (!className
    ? (item['primary_kind'] as string | undefined)
    : undefined);

  const value = classSlug ?? kind;
  const label =
    className ??
    (kind ? CONCEPT_KIND_LABELS[kind as keyof typeof CONCEPT_KIND_LABELS] ?? kind : null);
  const color = className
    ? CLASS_COLOR(classSlug)
    : kind
      ? KIND_COLORS[kind as keyof typeof KIND_COLORS] ?? '#6b7280'
      : '#6b7280';
  const active = value ? activeValues?.includes(value) : false;

  if (!label) return null;

  const cls = `inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${
    onClick ? 'cursor-pointer ring-1 ring-inset ring-transparent hover:ring-current' : ''
  }`;
  const style = { backgroundColor: `${color}1a`, color, ...(active ? { boxShadow: `inset 0 0 0 2px ${color}` } : {}) };
  if (onClick && value) {
    return (
      <button type="button" onClick={() => onClick(value)} className={cls} style={style} title={value}>
        <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />
        {label}
      </button>
    );
  }
  return (
    <span className={cls} style={style} title={value}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
};

type SortKey = 'name' | 'updated' | 'relations';
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

interface CatalogBrowserProps {
  items: CatalogItem[];
  loading: boolean;
  total: number;
  viewMode: 'list' | 'card';
  selectedItemId?: string;
  onSelectItem: (id: string) => void;
  domainRoute: (item: CatalogItem) => string | null;
  /** Active page-search term (highlighted in title/description). */
  searchTerm?: string;
  /** Click a scope/class tag to drive the workspace filters. */
  onScopeClick?: (scope: string) => void;
  onClassClick?: (slug: string) => void;
  /** Currently-applied scope filter (to mark a tag active). */
  activeScope?: string;
  /** Currently-applied class slugs (to mark a tag active). */
  activeClasses?: string[];
  /** True when any search/scope/class filter is set (drives the empty state). */
  hasActiveFilters?: boolean;
  onClearFilters?: () => void;
  /** Incremental pagination (Load more). */
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  /** Picker mode: ids already picked (highlighted + marked "Added"). */
  pickedIds?: string[];
  /** Picker mode: toggle an item into/out of the selection. When provided,
   *  each row renders an Add/Added button. Off by default (workspace use). */
  onTogglePick?: (item: CatalogItem) => void;
}

const itemLabel = (item: CatalogItem): string =>
  String(item.name || item.slug || item.id || 'Unnamed');

export const CatalogBrowser: React.FC<CatalogBrowserProps> = ({
  items,
  loading,
  total,
  viewMode,
  selectedItemId,
  onSelectItem,
  domainRoute,
  searchTerm,
  onScopeClick,
  onClassClick,
  activeScope,
  activeClasses,
  hasActiveFilters,
  onClearFilters,
  hasMore,
  loadingMore,
  onLoadMore,
  pickedIds,
  onTogglePick,
}) => {
  const { t, i18n } = useTranslation();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
  const locale: Locale = i18n.language.startsWith('el') ? el : enUS;
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const [sortBy, setSortBy] = useState<SortKey>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const toggleSort = (key: SortKey) => {
    if (sortBy === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortBy(key);
      setSortDir(key === 'updated' || key === 'relations' ? 'desc' : 'asc');
    }
  };

  const sortedItems = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...items].sort((a, b) => {
      if (sortBy === 'updated') {
        const ta = a.updated_at ? new Date(a.updated_at).getTime() : 0;
        const tb = b.updated_at ? new Date(b.updated_at).getTime() : 0;
        return (ta - tb) * dir;
      }
      if (sortBy === 'relations') {
        const ra = typeof a.relation_count === 'number' ? a.relation_count : 0;
        const rb = typeof b.relation_count === 'number' ? b.relation_count : 0;
        return (ra - rb) * dir;
      }
      return itemLabel(a).localeCompare(itemLabel(b)) * dir;
    });
  }, [items, sortBy, sortDir]);

  /** Scope filter value this item's badge would set, or null if not clickable. */
  const scopeClickValue = (item: CatalogItem): string | null => {
    if (item.scope === 'system') return 'system';
    if (item.scope === 'tenant') return 'tenant';
    if (item.scope === 'user' && item.created_by && currentUserId && item.created_by === currentUserId)
      return 'mine';
    return null;
  };

  /** Keyboard navigation: ↑/↓ moves selection, Esc clears it. */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (sortedItems.length === 0) return;
    const curIdx = selectedItemId
      ? sortedItems.findIndex((it) => String(it.id) === selectedItemId)
      : -1;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = sortedItems[Math.min(curIdx + 1, sortedItems.length - 1)];
      if (next) onSelectItem(String(next.id));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = sortedItems[Math.max(curIdx - 1, 0)];
      if (prev) onSelectItem(String(prev.id));
    } else if (e.key === 'Escape' && selectedItemId) {
      e.preventDefault();
      onSelectItem('');
    }
  };

  // Keep the selected row in view (also helps keyboard nav). `nearest` only
  // scrolls when the row is off-screen, so click selection isn't disturbed.
  useEffect(() => {
    if (!selectedItemId || !scrollRef.current) return;
    const el = scrollRef.current.querySelector(
      `[data-item-id="${CSS.escape(selectedItemId)}"]`,
    );
    el?.scrollIntoView({ block: 'nearest' });
  }, [selectedItemId]);

  return (
    <div className="flex flex-col h-full min-h-0 gap-2">
      {/* Sticky sort + count header (sits above the scroll area) */}
      <div className="shrink-0 flex items-center justify-between gap-2 px-1">
        <div className="flex items-center gap-1">
          {(['name', 'updated', 'relations'] as SortKey[]).map((key) => {
            const activeSort = sortBy === key;
            const Icon = !activeSort ? ArrowUpDown : sortDir === 'asc' ? ArrowUp : ArrowDown;
            const fallback = key === 'name' ? 'Name' : key === 'updated' ? 'Updated' : 'Relations';
            return (
              <button
                key={key}
                onClick={() => toggleSort(key)}
                className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
                  activeSort
                    ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300'
                    : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                <Icon className="w-3 h-3" />
                {t(`catalogs.sort_${key}`, fallback)}
              </button>
            );
          })}
        </div>
        <span className="text-[11px] text-gray-400 shrink-0">
          {items.length === total
            ? `${total} ${t('catalogs.total', 'total')}`
            : t('catalogs.count_of', { count: items.length, total, defaultValue: `${items.length} of ${total}` })}
        </span>
      </div>

      <div
        ref={scrollRef}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        title={t('catalogs.keyboard_hint', '↑↓ navigate · Esc clear')}
        className="flex-1 overflow-y-auto min-h-0 custom-scrollbar outline-none focus:ring-1 focus:ring-blue-200 dark:focus:ring-blue-900 rounded-lg"
      >
      {/* Items */}
      {loading ? (
        viewMode === 'list' ? (
          <ul className="divide-y divide-gray-100 dark:divide-gray-700 rounded-lg border border-gray-200 dark:border-gray-700">
            {Array.from({ length: 7 }).map((_, i) => (
              <li key={i} className="flex items-center gap-3 px-4 py-3.5">
                <div className="flex-1 space-y-2">
                  <div className="h-3.5 w-2/3 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
                  <div className="h-3 w-1/2 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <div className="h-3.5 w-12 bg-gray-100 dark:bg-gray-800 rounded-full animate-pulse" />
                  <div className="h-3.5 w-10 bg-gray-100 dark:bg-gray-800 rounded-full animate-pulse" />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 flex flex-col gap-2 min-h-[7rem]"
              >
                <div className="space-y-2">
                  <div className="h-3.5 w-3/4 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
                  <div className="h-3 w-full bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
                </div>
                <div className="mt-auto pt-2 border-t border-gray-100 dark:border-gray-700/60">
                  <div className="h-3 w-1/3 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        )
      ) : sortedItems.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-14 px-4 text-center">
          {hasActiveFilters ? (
            <>
              <SearchX className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
              <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                {t('catalogs.no_matches', 'No matches')}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {t('catalogs.no_matches_hint', 'Try adjusting your search or filters.')}
              </p>
              {onClearFilters && (
                <button
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
                {t('catalogs.empty_title', 'No items yet')}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {t('catalogs.empty_hint', 'Use the New button to create one.')}
              </p>
            </>
          )}
        </div>
      ) : viewMode === 'list' ? (
        <ul className="divide-y divide-gray-100 dark:divide-gray-700 rounded-lg border border-gray-200 dark:border-gray-700">
          {sortedItems.map((item) => {
            const updated = item.updated_at
              ? formatDistanceToNow(new Date(item.updated_at), { addSuffix: true, locale })
              : null;
            const relCount = typeof item.relation_count === 'number' ? item.relation_count : null;
            const isPicked = !!pickedIds?.includes(String(item.id));
            const active = (selectedItemId && String(item.id) === selectedItemId) || isPicked;
            const route = domainRoute(item);
            const scopeVal = scopeClickValue(item);
            // Strip any HTML/Markdown in the description to a single plain-text
            // line for the compact row — never formatted prose in the list.
            const desc = toPlainText(item.description);
            return (
              <li
                key={String(item.id)}
                data-item-id={String(item.id)}
                onClick={() => onSelectItem(String(item.id))}
                className={`flex items-start gap-3 px-4 py-3.5 cursor-pointer ${
                  active
                    ? 'bg-blue-50 dark:bg-blue-900/30 shadow-[inset_3px_0_0_0_#3b82f6]'
                    : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'
                }`}
              >
                <div className="flex-1 text-left min-w-0">
                  <span className="font-semibold text-sm block truncate">
                    <Highlight text={itemLabel(item)} term={searchTerm} />
                  </span>
                  {desc ? (
                    <span className="block text-xs text-gray-500 dark:text-gray-400 line-clamp-2 leading-snug mt-1">
                      <Highlight text={desc} term={searchTerm} />
                    </span>
                  ) : null}
                  {(updated || relCount !== null) && (
                    <span className="block text-[11px] text-gray-400 mt-1.5">
                      {updated && <>{t('catalogs.updated_ago', 'Updated')} {updated}</>}
                      {updated && relCount ? ' · ' : ''}
                      {relCount !== null && (
                        <span className="inline-flex items-center gap-0.5">
                          <GitBranch className="w-3 h-3" /> {relCount}
                        </span>
                      )}
                    </span>
                  )}
                </div>
                <div className="flex flex-col items-end gap-1.5 shrink-0">
                  <div className="flex flex-col items-end gap-1" onClick={(e) => e.stopPropagation()}>
                    <ClassBadge
                      item={item}
                      activeValues={activeClasses}
                      onClick={onClassClick}
                    />
                    <ScopeBadge
                      scope={item.scope}
                      created_by={item.created_by}
                      currentUserId={currentUserId}
                      onClick={
                        onScopeClick && scopeVal
                          ? () => onScopeClick(scopeVal)
                          : undefined
                      }
                      active={!!scopeVal && activeScope === scopeVal}
                    />
                  </div>
                  {route && (
                    <a
                      href={route}
                      onClick={(e) => e.stopPropagation()}
                      className="p-1.5 text-gray-400 hover:text-blue-500"
                      title={t('catalogs.open_in_domain', 'Open in domain view')}
                    >
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  )}
                  {onTogglePick && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onTogglePick(item); }}
                      className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
                        isPicked
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                          : 'bg-blue-600 text-white hover:bg-blue-700'
                      }`}
                    >
                      {isPicked ? (
                        <>
                          <Check className="w-3 h-3" /> {t('catalogs.picker_added', 'Added')}
                        </>
                      ) : (
                        <>
                          <Plus className="w-3 h-3" /> {t('common.add', 'Add')}
                        </>
                      )}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          {sortedItems.map((item) => {
            const updated = item.updated_at
              ? formatDistanceToNow(new Date(item.updated_at), { addSuffix: true, locale })
              : null;
            const relCount = typeof item.relation_count === 'number' ? item.relation_count : null;
            const isPicked = !!pickedIds?.includes(String(item.id));
            const active = (selectedItemId && String(item.id) === selectedItemId) || isPicked;
            const route = domainRoute(item);
            const scopeVal = scopeClickValue(item);
            // Strip any HTML/Markdown in the description to a single plain-text
            // line for the compact tile — never formatted prose in the grid.
            const desc = toPlainText(item.description);
            return (
              <div
                key={String(item.id)}
                data-item-id={String(item.id)}
                onClick={() => onSelectItem(String(item.id))}
                className={`rounded-xl border p-4 flex flex-col gap-2 min-h-[7rem] transition-all cursor-pointer ${
                  active
                    ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/30 ring-1 ring-inset ring-blue-300 dark:ring-blue-700'
                    : 'border-gray-200 dark:border-gray-700 hover:border-blue-300 hover:shadow-sm'
                }`}
              >
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  <div className="text-left min-w-0 flex-1">
                    <span className="font-semibold text-sm block break-words">
                      <Highlight text={itemLabel(item)} term={searchTerm} />
                    </span>
                    {desc ? (
                      <span className="block text-xs text-gray-500 dark:text-gray-400 line-clamp-2 mt-1">
                        <Highlight text={desc} term={searchTerm} />
                      </span>
                    ) : null}
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                    <ClassBadge
                      item={item}
                      activeValues={activeClasses}
                      onClick={onClassClick}
                    />
                    <ScopeBadge
                      scope={item.scope}
                      created_by={item.created_by}
                      currentUserId={currentUserId}
                      onClick={
                        onScopeClick && scopeVal
                          ? () => onScopeClick(scopeVal)
                          : undefined
                      }
                      active={!!scopeVal && activeScope === scopeVal}
                    />
                  </div>
                </div>
                <div className="flex items-end justify-between gap-2 pt-2 mt-1 border-t border-gray-100 dark:border-gray-700/60">
                  <div className="flex items-center gap-2 text-[11px] text-gray-400 min-w-0">
                    {updated && <span className="truncate">{t('catalogs.updated_ago', 'Updated')} {updated}</span>}
                    {relCount !== null && (
                      <span className="inline-flex items-center gap-0.5 shrink-0">
                        <GitBranch className="w-3 h-3" /> {relCount}
                      </span>
                    )}
                  </div>
                  {route && (
                    <a
                      href={route}
                      onClick={(e) => e.stopPropagation()}
                      className="p-1.5 text-gray-400 hover:text-blue-500 shrink-0"
                      title={t('catalogs.open_in_domain', 'Open in domain view')}
                    >
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  )}
                  {onTogglePick && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onTogglePick(item); }}
                      className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md shrink-0 transition-colors ${
                        isPicked
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                          : 'bg-blue-600 text-white hover:bg-blue-700'
                      }`}
                    >
                      {isPicked ? (
                        <>
                          <Check className="w-3 h-3" /> {t('catalogs.picker_added', 'Added')}
                        </>
                      ) : (
                        <>
                          <Plus className="w-3 h-3" /> {t('common.add', 'Add')}
                        </>
                      )}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {hasMore && !loading && sortedItems.length > 0 && (
        <div className="flex justify-center py-4">
          <button
            onClick={onLoadMore}
            disabled={loadingMore || !onLoadMore}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {loadingMore ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('catalogs.loading_more', 'Loading…')}
              </>
            ) : (
              <>
                <ChevronDown className="w-4 h-4" />
                {t('catalogs.load_more', 'Load more')}
              </>
            )}
          </button>
        </div>
      )}
      </div>
    </div>
  );
};
