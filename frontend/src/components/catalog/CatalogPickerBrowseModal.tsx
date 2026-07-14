/**
 * Catalog picker — browse modal (the popup half of `CatalogItemPicker`).
 *
 * Opened by the in-input "browse" button. Lets the user pick items from the
 * full catalog instead of the inline type-ahead search: a searchable catalog-
 * type dropdown (default **All** → cross-catalog search), a search box, a scope
 * filter, and the shared {@link CatalogBrowser} list with an Add/Added toggle
 * per row. Supports accumulating multiple picks before Done.
 *
 * "All" uses the cross-catalog {@link searchCatalogs} endpoint; a specific type
 * uses the per-type {@link listCatalogItems} (so scope + pagination apply).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, X, List, GitBranch } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { LoadingState } from '../ui/LoadingState';
import { CatalogTypeSelect } from './CatalogTypeSelect';
import { CatalogBrowser } from './CatalogBrowser';
import { CatalogPickerGraph } from './CatalogPickerGraph';
import { FilterBar } from '../ui/filters/FilterBar';
import { useFilterState } from '../ui/filters/useFilterState';
import { getFacetsForType } from './catalogFacetRegistry';
import {
  listCatalogItems,
  listCatalogTypes,
  searchCatalogs,
} from '../../services/catalogService';
import type {
  CatalogItem,
  CatalogSelection,
  CatalogTypeMeta,
} from '../../types/catalog';

interface CatalogPickerBrowseModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Current selection (so rows render Added + the footer count is live). */
  picked: CatalogSelection[];
  /** Toggle a visible item into/out of the selection. */
  onTogglePick: (item: CatalogItem, catalogType: string) => void;
  /** Restrict the type dropdown to a subset; default = all registered. */
  allowedTypes?: string[];
  /** ConceptKind value that narrows the `concept` catalog (passed through from
   *  `CatalogItemPicker.conceptKind`). Ignored for non-concept catalogs. */
  conceptKind?: string;
  /** 'single' hides the multi-accumulate affordance. */
  mode?: 'single' | 'multi';
}

const ALL = 'all';
const PAGE_SIZE = 50;

export const CatalogPickerBrowseModal: React.FC<CatalogPickerBrowseModalProps> = ({
  isOpen,
  onClose,
  picked,
  onTogglePick,
  allowedTypes,
  conceptKind,
  mode = 'multi',
}) => {
  const { t } = useTranslation();
  const [types, setTypes] = useState<CatalogTypeMeta[]>([]);
  // When the picker is scoped to a single distinctive catalog (e.g. a
  // body-location field locked to anatomy), default to that catalog and
  // disable the "All" option — it's redundant (All == the one catalog) and
  // surfacing cross-catalog results the user can't select would be confusing.
  const singleType =
    allowedTypes && allowedTypes.length === 1 ? allowedTypes[0] : null;
  const [activeType, setActiveType] = useState<string>(singleType ?? ALL);

  const [items, setItems] = useState<CatalogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const [search, setSearch] = useState('');
  const [scope, setScope] = useState('');
  // List vs graph selection surface. Graph gives relational context (e.g. the
  // anatomy hierarchy helps you navigate to the right organ); list is the flat
  // browse/sort view. Both surfaces feed the same `handleToggle` → selection.
  const [viewMode, setViewMode] = useState<'list' | 'graph'>('list');

  // Load the catalog-type registry once (filtered to allowedTypes).
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await listCatalogTypes();
        if (cancelled) return;
        const filtered = allowedTypes?.length
          ? resp.types.filter((tg) => allowedTypes.includes(tg.type))
          : resp.types;
        setTypes(filtered);
      } catch {
        if (!cancelled) setTypes([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen, allowedTypes]);

  // Reset the active type when the modal reopens, so a picker repurposed for a
  // different field (different `allowedTypes`) defaults correctly: locked to
  // the single catalog when distinctive, or back to "All" otherwise.
  useEffect(() => {
    if (!isOpen) return;
    setActiveType(singleType ?? ALL);
  }, [isOpen, singleType]);

  // Resolve a visible item's catalog type for the toggle callback. In "all"
  // mode the list comes from searchCatalogs (no per-item type on CatalogItem),
  // so we tag rows via a side index built at fetch time.
  const itemToType = useMemo(() => {
    const map: Record<string, string> = {};
    items.forEach((it) => {
      // `__type` is stamped on at fetch for cross-catalog search results.
      const tag = (it as CatalogItem & { __type?: string }).__type;
      if (tag) map[String(it.id)] = tag;
    });
    return map;
  }, [items]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (activeType === ALL) {
        // The cross-catalog search endpoint requires q (min_length=2). With a
        // shorter query there's nothing to list — show a prompt instead.
        if (search.trim().length < 2) {
          setItems([]);
          setTotal(0);
          return;
        }
        // "All" means "all ALLOWED catalogs", not "every catalog in the
        // system" — pass `types` so a scoped picker never leaks results from
        // catalogs the field can't accept.
        const resp = await searchCatalogs(search, {
          limit: PAGE_SIZE,
          types: allowedTypes?.length ? allowedTypes.join(',') : undefined,
          kind: conceptKind,
        });
        // Cross-catalog hits → CatalogItem-shaped rows tagged with __type.
        const rows = resp.results.map(
          (r) =>
            ({
              id: r.id,
              name: r.label,
              __type: r.type,
            }) as CatalogItem,
        );
        setItems(rows);
        setTotal(rows.length);
      } else {
        const resp = await listCatalogItems(activeType, {
          search: search || undefined,
          scope: scope || undefined,
          // kind only meaningful for the concept catalog; the backend ignores
          // it for other types. listCatalogItems already supports a kind param.
          kind: conceptKind,
          limit: PAGE_SIZE,
          offset: 0,
        });
        setItems(resp.items);
        setTotal(resp.total);
      }
    } catch {
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [activeType, search, scope, conceptKind, allowedTypes]);

  useEffect(() => {
    if (!isOpen) return;
    const t = setTimeout(load, 200); // debounce search/scope/type changes
    return () => clearTimeout(t);
  }, [load, isOpen]);

  const loadMore = useCallback(async () => {
    if (loadingMore || activeType === ALL) return; // search endpoint has no offset
    setLoadingMore(true);
    try {
      const resp = await listCatalogItems(activeType, {
        search: search || undefined,
        scope: scope || undefined,
        kind: conceptKind,
        limit: PAGE_SIZE,
        offset: items.length,
      });
      setItems((prev) => [...prev, ...resp.items]);
    } catch {
      /* keep what we have */
    } finally {
      setLoadingMore(false);
    }
  }, [activeType, search, scope, conceptKind, items.length, loadingMore]);

  const pickedIds = useMemo(() => picked.map((p) => p.id), [picked]);

  const handleToggle = useCallback(
    (item: CatalogItem, explicitType?: string) => {
      // The graph view passes the node's catalog type directly (it knows it
      // from the node). The list view omits it; we resolve from the __type
      // tag stamped at fetch, falling back to the active type dropdown.
      const cat = explicitType ?? itemToType[String(item.id)] ?? activeType;
      onTogglePick(item, cat);
    },
    [itemToType, activeType, onTogglePick],
  );

  const hasMore = activeType !== ALL && items.length < total;

  // Per-type client-mode facets (biomarker category/telemetry, allergy
  // category, vaccine coding_system, medication is_custom, concept status).
  // Server-mode facets (concept kind, anatomy class) are excluded — the picker
  // filters in-memory and doesn't refetch on facet change. Facets are empty
  // for "All" (cross-catalog) mode and for types with no client facets.
  const facets = useMemo(
    () => (activeType !== ALL ? getFacetsForType(activeType).filter((f) => f.mode !== 'server') : []),
    [activeType],
  );
  const pickerFilter = useFilterState(facets);
  const filteredItems = useMemo(
    () => pickerFilter.applyFilters(items),
    [pickerFilter, items],
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('catalogs.picker_browse_title', 'Browse catalog')}
      className="max-w-3xl"
    >
      <div className="flex flex-col h-[70vh] gap-3">
        {/* Top row: list filters (hidden in graph mode — the graph owns its
            own type/kind/depth/relation filter strip) + the List|Graph toggle. */}
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {viewMode === 'list' && (
            <>
              <CatalogTypeSelect
                types={types}
                activeType={activeType}
                onSelect={(tp) => setActiveType(tp)}
                allowAll
                allDisabled={!!singleType}
                allValue={ALL}
                allLabel={t('catalogs.picker_type_all', 'All catalogs')}
              />

              {/* Scope segmented control — list view + concrete type only. */}
              {activeType !== ALL && (
                <div className="flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
                  {[
                    { v: '', k: 'catalogs.scope_all', f: 'All' },
                    { v: 'system', k: 'catalogs.scope_system', f: 'System' },
                    { v: 'tenant', k: 'catalogs.scope_tenant', f: 'Tenant' },
                    { v: 'mine', k: 'catalogs.scope_mine', f: 'Mine' },
                  ].map((opt) => (
                    <button
                      key={opt.v || 'all'}
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

              <div className="relative flex-1 min-w-[12rem]">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('catalogs.picker_search', 'Search items…')}
                  className="w-full pl-8 pr-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none"
                />
                {search && (
                  <button
                    onClick={() => setSearch('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 text-gray-400 hover:text-gray-600"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </>
          )}

          {/* List | Graph view toggle (always visible). */}
          <div className={`flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden shrink-0 ${viewMode === 'graph' ? 'ml-auto' : ''}`}>
            <button
              onClick={() => setViewMode('list')}
              title={t('catalogs.picker_view_list', 'List view')}
              className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium ${
                viewMode === 'list'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
              }`}
            >
              <List className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode('graph')}
              title={t('catalogs.picker_view_graph', 'Graph view')}
              className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium ${
                viewMode === 'graph'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
              }`}
            >
              <GitBranch className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Selection surface: graph (relational, self-filtered) or list (flat). */}
        <div className="flex-1 min-h-0">
          {viewMode === 'graph' ? (
            <CatalogPickerGraph
              allowedTypes={allowedTypes}
              conceptKind={conceptKind}
              pickedIds={pickedIds}
              onTogglePick={handleToggle}
              mode={mode}
            />
          ) : loading ? (
            <LoadingState variant="section" message={t('catalogs.loading', 'Loading catalogs…')} />
          ) : activeType === ALL && search.trim().length < 2 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <Search className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
              <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                {t('catalogs.picker_all_hint_title', 'Search across all catalogs')}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {t('catalogs.picker_all_hint', 'Type at least 2 characters, or pick a specific catalog type to browse it.')}
              </p>
            </div>
          ) : (
            <div className="flex flex-col h-full gap-2">
              {/* Per-type facet chips (biomarker category/telemetry, allergy
                  category, etc.) — same FilterBar the workspace uses. Empty
                  (renders nothing) for types with no registered facets. */}
              {facets.length > 0 && (
                <FilterBar
                  facets={facets}
                  filter={pickerFilter}
                  items={items}
                  showActivePills
                  resultCount={filteredItems.length}
                  totalCount={items.length}
                />
              )}
              <div className="flex-1 min-h-0">
                <CatalogBrowser
                  items={filteredItems}
                  loading={false}
                  total={total}
                  viewMode="list"
                  domainRoute={() => null}
                  onSelectItem={() => undefined}
                  pickedIds={pickedIds}
                  onTogglePick={handleToggle}
                  hasMore={hasMore}
                  loadingMore={loadingMore}
                  onLoadMore={loadMore}
                  searchTerm={search}
                  hasActiveFilters={!loading && (!!search || !!scope || pickerFilter.isActive)}
                  onClearFilters={() => {
                    setSearch('');
                    setScope('');
                    pickerFilter.clearAll();
                  }}
                />
              </div>
            </div>
          )}
        </div>

        {/* Footer: count + Done */}
        <div className="shrink-0 flex items-center justify-between gap-2 pt-2 border-t border-gray-100 dark:border-gray-700">
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {picked.length === 0
              ? t('catalogs.picker_none_selected', 'No items selected')
              : t('catalogs.picker_n_selected', { count: picked.length, defaultValue: '{{count}} selected' })}
          </span>
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700"
          >
            {mode === 'single'
              ? picked.length
                ? t('common.done', 'Done')
                : t('common.cancel', 'Cancel')
              : t('common.done', 'Done')}
          </button>
        </div>
      </div>
    </Modal>
  );
};
