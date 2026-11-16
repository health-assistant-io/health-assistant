/**
 * Catalog picker — browse-modal composition (the app-side half of
 * {@link CatalogItemPicker}).
 *
 * The browse-modal pattern (search / category filter / Add-Added rows /
 * pagination / count footer) lives in the shared `multi-select-picker`
 * module. This module supplies the catalog-domain chrome and the data
 * closures through that module's slots — `renderBody` (body composition),
 * `renderRowContent` (rich rows), `filterOption` (client facets),
 * `reloadKey` (server-side scope refetch) and controlled `category`:
 *
 *   - the icon + searchable catalog-type dropdown (`CatalogTypeSelect`),
 *     with "All" locked when the field allows a single catalog;
 *   - the scope filter (All / System / Tenant / Mine) — server-side, so a
 *     change re-runs the current search via `reloadKey`;
 *   - per-type client facets (biomarker category/telemetry, allergy
 *     category, vaccine coding_system, medication ownership, concept
 *     status) via `filterOption`;
 *   - the List | Graph view toggle — the graph selection surface
 *     (`CatalogPickerGraph`) rides `renderBody`, toggling through the
 *     body ctx so both surfaces feed the same controlled selection.
 *
 * "All" uses the cross-catalog {@link searchCatalogs} endpoint (min 2
 * chars — the hint state renders through `renderBody` too); a specific
 * type uses {@link listCatalogItems} (scope + pagination apply).
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GitBranch, List, Plus, Search, X } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import type {
  MultiSelectPickerBrowseProps,
  PickerBrowseBodyCtx,
  PickerOption,
} from '../ui/multiSelectPicker';
import { CatalogTypeSelect } from './CatalogTypeSelect';
import { CatalogPickerGraph } from './CatalogPickerGraph';
import { ClassBadge, Highlight, itemLabel } from './CatalogBrowser';
import { ScopeBadge } from './ScopeBadge';
import { FilterBar } from '../ui/filters/FilterBar';
import { useFilterState } from '../ui/filters/useFilterState';
import { getFacetsForType } from './catalogFacetRegistry';
import { CreateCatalogItemModal } from './CreateCatalogItemModal';
import {
  listCatalogItems,
  listCatalogTypes,
  searchCatalogs,
} from '../../services/catalogService';
import { toPlainText } from '../../utils/textFormat';
import { useAuthStore } from '../../store/slices/authSlice';
import {
  hitToOption,
  itemToOption,
  type CatalogPickerOption,
} from './catalogPickerAdapter';
import type { CatalogItem, CatalogTypeMeta } from '../../types/catalog';

const ALL = '';
const PAGE_SIZE = 50;

const SCOPE_OPTIONS = [
  { v: '', k: 'catalogs.scope_all', f: 'All' },
  { v: 'system', k: 'catalogs.scope_system', f: 'System' },
  { v: 'tenant', k: 'catalogs.scope_tenant', f: 'Tenant' },
  { v: 'mine', k: 'catalogs.scope_mine', f: 'Mine' },
];

export interface UseCatalogPickerBrowseOpts {
  /** Catalog types the field accepts (already stringified). */
  allowedList?: string[];
  conceptKind?: string;
  /** Relation stamped on new picks in relation mode; undefined = no relation. */
  relationDefault?: string;
  /** The field's selection mode — create is offered in `single` mode only. */
  mode?: 'single' | 'multi';
  /** Opt-in: render **New <type>** in the browse list header, opening
   *  {@link CreateCatalogItemModal} (the one manual definition surface).
   *  Gated to a single allowed type + single mode + list view. */
  allowCreate?: boolean;
}

export function useCatalogPickerBrowse({
  allowedList,
  conceptKind,
  relationDefault,
  mode,
  allowCreate = false,
}: UseCatalogPickerBrowseOpts): {
  browseModalProps: MultiSelectPickerBrowseProps;
  onBrowseOpenChange: (open: boolean) => void;
  /** Render beside the picker — the create-definition dialog the New action
   *  opens (empty node when create is not offered). */
  createDialog: React.ReactNode;
} {
  const { t, i18n } = useTranslation();
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
  const locale: Locale = i18n.language.startsWith('el') ? el : enUS;

  const singleType =
    allowedList && allowedList.length === 1 ? allowedList[0] : null;
  const canCreate = allowCreate && !!singleType && mode === 'single';

  const [category, setCategory] = useState<string>(singleType ?? ALL);
  const [scope, setScope] = useState('');
  const [viewMode, setViewMode] = useState<'list' | 'graph'>('list');
  const [types, setTypes] = useState<CatalogTypeMeta[]>([]);
  const [loadedItems, setLoadedItems] = useState<CatalogItem[]>([]);
  const [loadedTotal, setLoadedTotal] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const typesFetchSeq = useRef(0);
  // The browse body ctx, captured from renderBody so the create dialog's
  // onCreated can select the new row through the same toggle the rows use.
  const bodyCtxRef = useRef<PickerBrowseBodyCtx | null>(null);

  // Per-type client-mode facets (server-mode facets like concept kind are
  // excluded — the picker filters in-memory and doesn't refetch on facet
  // change). Empty for "All" (cross-catalog) mode.
  const facets = useMemo(
    () =>
      category !== ALL
        ? getFacetsForType(category).filter((f) => f.mode !== 'server')
        : [],
    [category],
  );
  const pickerFilter = useFilterState(facets);

  const onBrowseOpenChange = useCallback(
    (open: boolean) => {
      if (!open) return;
      setCategory(singleType ?? ALL);
      setLoadedItems([]);
      setLoadedTotal(0);
      const seq = ++typesFetchSeq.current;
      (async () => {
        try {
          const resp = await listCatalogTypes();
          if (seq !== typesFetchSeq.current) return;
          setTypes(
            allowedList?.length
              ? resp.types.filter((tg) => allowedList.includes(tg.type))
              : resp.types,
          );
        } catch {
          if (seq === typesFetchSeq.current) setTypes([]);
        }
      })();
    },
    [singleType, allowedList],
  );

  const fetchPage = useCallback(
    async (query: string, cat: string, offset: number): Promise<PickerOption[]> => {
      try {
        if (cat === ALL) {
          if (offset > 0) return [];
          if (query.trim().length < 2) {
            setLoadedItems([]);
            return [];
          }
          const resp = await searchCatalogs(query, {
            limit: PAGE_SIZE,
            types: allowedList?.length ? allowedList.join(',') : undefined,
            kind: conceptKind,
          });
          return resp.results.map((hit) => hitToOption(hit, relationDefault));
        }
        const resp = await listCatalogItems(cat, {
          search: query || undefined,
          scope: scope || undefined,
          kind: conceptKind,
          limit: PAGE_SIZE,
          offset,
        });
        setLoadedItems((prev) => (offset === 0 ? resp.items : [...prev, ...resp.items]));
        setLoadedTotal(resp.total);
        return resp.items.map((item) => itemToOption(item, cat, relationDefault));
      } catch {
        return [];
      }
    },
    [allowedList, conceptKind, scope, relationDefault],
  );

  const onSearch = useCallback(
    (query: string, cat: string | null) => fetchPage(query, cat ?? ALL, 0),
    [fetchPage],
  );

  const onLoadMore = useCallback(
    (query: string, cat: string | null, offset: number) =>
      fetchPage(query, cat ?? ALL, offset),
    [fetchPage],
  );

  const filterOption = useCallback(
    (option: PickerOption) => {
      const item = (option as CatalogPickerOption).item;
      return item ? pickerFilter.matches(item) : true;
    },
    [pickerFilter],
  );

  const renderRowContent = useCallback(
    (
      option: PickerOption,
      _selected: boolean,
      rowCtx: { search: string; category: string },
    ): React.ReactNode => {
      const item = (option as CatalogPickerOption).item;
      if (!item) {
        return (
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="w-20 shrink-0 text-[10px] font-bold uppercase tracking-wide text-blue-500">
              {option.category}
            </span>
            <span className="min-w-0 flex-1 truncate">
              <Highlight text={option.label} term={rowCtx.search} />
            </span>
          </div>
        );
      }
      const desc = toPlainText(item.description);
      const updated = item.updated_at
        ? formatDistanceToNow(new Date(item.updated_at), { addSuffix: true, locale })
        : null;
      const relCount = typeof item.relation_count === 'number' ? item.relation_count : null;
      return (
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="min-w-0 flex-1 text-left">
            <span className="block truncate text-sm font-semibold">
              <Highlight text={itemLabel(item)} term={rowCtx.search} />
            </span>
            {desc ? (
              <span className="mt-1 block line-clamp-2 text-xs leading-snug text-gray-500 dark:text-gray-400">
                <Highlight text={desc} term={rowCtx.search} />
              </span>
            ) : null}
            {(updated || relCount !== null) && (
              <span className="mt-1.5 block text-[11px] text-gray-400">
                {updated && (
                  <>
                    {t('catalogs.updated_ago', 'Updated')} {updated}
                  </>
                )}
                {updated && relCount ? ' · ' : ''}
                {relCount !== null && (
                  <span className="inline-flex items-center gap-0.5">
                    <GitBranch className="w-3 h-3" /> {relCount}
                  </span>
                )}
              </span>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <ClassBadge item={item} />
            <ScopeBadge
              scope={item.scope}
              created_by={item.created_by}
              currentUserId={currentUserId}
            />
          </div>
        </div>
      );
    },
    [t, locale, currentUserId],
  );

  const renderBody = useCallback(
    (ctx: PickerBrowseBodyCtx): React.ReactNode => {
      // Latest-ctx ref: the shared picker invokes renderBody during its own
      // render, so there is no effect boundary to move this into. The write
      // is idempotent (same ctx object within one render pass) — the guard
      // keeps StrictMode's double render from touching anything.
      if (bodyCtxRef.current !== ctx) bodyCtxRef.current = ctx;
      const pickedIds = ctx.value.map((entry) => {
        const ext = entry as CatalogPickerOption;
        return ext.id ?? String(entry.value).split(':')[1];
      });
      const viewToggle = (
        <div
          className={`flex shrink-0 items-center overflow-hidden rounded-lg border border-gray-300 dark:border-gray-600 ${viewMode === 'graph' ? 'ml-auto' : ''}`}
        >
          <button
            type="button"
            onClick={() => setViewMode('list')}
            title={t('catalogs.picker_view_list', 'List view')}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium ${
              viewMode === 'list'
                ? 'bg-blue-600 text-white'
                : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
          >
            <List className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setViewMode('graph')}
            title={t('catalogs.picker_view_graph', 'Graph view')}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium ${
              viewMode === 'graph'
                ? 'bg-blue-600 text-white'
                : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
          >
            <GitBranch className="h-3.5 w-3.5" />
          </button>
        </div>
      );

      if (viewMode === 'graph') {
        return (
          <div className="flex h-[70vh] min-h-0 flex-col gap-2">
            {viewToggle}
            <div className="min-h-0 flex-1">
              <CatalogPickerGraph
                allowedTypes={allowedList}
                conceptKind={conceptKind}
                pickedIds={pickedIds}
                onTogglePick={(item, catalogType) =>
                  ctx.toggle(itemToOption(item, catalogType, relationDefault))
                }
              />
            </div>
          </div>
        );
      }

      const allHint = category === ALL && ctx.search.trim().length < 2;
      const hasListFilters = !!scope || !!ctx.search.trim() || pickerFilter.isActive;
      const createLabel = t('catalogs.create_new_item', 'New {{type}}', {
        type: t(
          `catalogs.${singleType}.label`,
          singleType ? singleType.charAt(0).toUpperCase() + singleType.slice(1) : '',
        ),
      });
      return (
        <div className="flex h-[70vh] min-h-0 flex-col gap-2">
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <CatalogTypeSelect
              types={types}
              activeType={category}
              onSelect={ctx.onCategoryChange}
              allowAll
              allDisabled={!!singleType}
              allValue={ALL}
              allLabel={t('catalogs.picker_type_all', 'All catalogs')}
            />
            {category !== ALL && (
              <div className="flex items-center overflow-hidden rounded-lg border border-gray-300 dark:border-gray-600">
                {SCOPE_OPTIONS.map((opt) => (
                  <button
                    key={opt.v || 'all'}
                    type="button"
                    onClick={() => setScope(opt.v)}
                    className={`px-2.5 py-1.5 text-xs font-medium ${
                      scope === opt.v
                        ? 'bg-blue-600 text-white'
                        : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
                    }`}
                  >
                    {t(opt.k, opt.f)}
                  </button>
                ))}
              </div>
            )}
            {hasListFilters && (
              <button
                type="button"
                onClick={() => {
                  setScope('');
                  ctx.onSearchChange('');
                  pickerFilter.clearAll();
                }}
                className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-gray-500 hover:bg-red-50 hover:text-red-600 dark:text-gray-300 dark:hover:bg-red-900/20"
              >
                <X className="h-3.5 w-3.5" />
                {t('common.clear_filters', 'Clear filters')}
              </button>
            )}
            <div className="relative min-w-48 flex-1">{ctx.searchInput}</div>
            {canCreate && (
              <button
                type="button"
                onClick={() => {
                  setCreateName(ctx.search.trim());
                  setCreateOpen(true);
                }}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-blue-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
              >
                <Plus className="h-3.5 w-3.5" />
                {createLabel}
              </button>
            )}
            {viewToggle}
          </div>
          {allHint ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
              <Search className="mb-2 h-8 w-8 text-gray-300 dark:text-gray-600" />
              <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                {t('catalogs.picker_all_hint_title', 'Search across all catalogs')}
              </p>
              <p className="mt-1 text-xs text-gray-400">
                {t(
                  'catalogs.picker_all_hint',
                  'Type at least 2 characters, or pick a specific catalog type to browse it.',
                )}
              </p>
            </div>
          ) : (
            <>
              {facets.length > 0 && (
                <FilterBar
                  facets={facets}
                  filter={pickerFilter}
                  items={loadedItems}
                  showActivePills
                  resultCount={pickerFilter.applyFilters(loadedItems).length}
                  totalCount={loadedItems.length}
                />
              )}
              {ctx.list}
            </>
          )}
        </div>
      );
    },
    [
      t,
      viewMode,
      category,
      scope,
      types,
      singleType,
      canCreate,
      facets,
      pickerFilter,
      loadedItems,
      allowedList,
      conceptKind,
      relationDefault,
    ],
  );

  const browseModalProps = useMemo<MultiSelectPickerBrowseProps>(
    () => ({
      title: t('catalogs.picker_browse_title', 'Browse catalog'),
      className: 'sm:max-w-3xl',
      debounceMs: 200,
      category,
      onCategoryChange: setCategory,
      reloadKey: scope,
      hasMore: category === ALL ? false : loadedItems.length < loadedTotal,
      filterOption,
      renderRowContent,
      renderBody,
      onSearch,
      onLoadMore,
    }),
    [
      t,
      category,
      scope,
      loadedItems.length,
      loadedTotal,
      filterOption,
      renderRowContent,
      renderBody,
      onSearch,
      onLoadMore,
    ],
  );

  /** The created definition becomes the pick (single mode selects + closes
   *  the browse modal — the same toggle the rows use). No reload bump: the
   *  next open refetches. */
  const handleCreated = useCallback(
    (row: CatalogItem) => {
      if (!singleType) return;
      bodyCtxRef.current?.toggle(itemToOption(row, singleType, relationDefault));
    },
    [singleType, relationDefault],
  );

  const createDialog = useMemo(
    () =>
      canCreate && singleType ? (
        <CreateCatalogItemModal
          isOpen={createOpen}
          onClose={() => setCreateOpen(false)}
          type={singleType}
          initialName={createName}
          onCreated={handleCreated}
        />
      ) : null,
    [canCreate, singleType, createOpen, createName, handleCreated],
  );

  return { browseModalProps, onBrowseOpenChange, createDialog };
}
