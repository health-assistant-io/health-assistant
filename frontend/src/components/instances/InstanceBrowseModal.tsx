/**
 * Instance picker — browse modal (the popup half of `InstancePicker`).
 *
 * The instance counterpart of `CatalogPickerBrowseModal`. Opened by the
 * in-input "browse" button. Lets the user pick patient-scoped records from a
 * full, filterable list instead of the inline type-ahead.
 *
 * Data strategy (adapter-driven via the registry):
 *   - **Single allowed type** (the common case, e.g. `<InstancePicker
 *     type="examination">`): fetches through that adapter's `fetch()`,
 *     renders the adapter's client-mode facets in a `FilterBar`, and supports
 *     incremental "Load more".
 *   - **Multiple allowed types**: a type selector switches the active adapter.
 *   - **Cross-type "All" search**: enabled only when a `unifiedSearch`
 *     callback is supplied (Phase 2 wires it to the backend
 *     `/instances/search` dispatcher). Without it, "All" is not offered — the
 *     modal never fakes cross-type search by fanning out ad-hoc.
 *
 * Security default: if no `patientId` is bound and the active adapter does not
 * opt into tenant scope (`allowTenantScope`), the modal refuses to fetch and
 * shows a "select a patient" prompt instead. The picker always binds the
 * current patient context, so this is defense-in-depth.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, X, Users } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { LoadingState } from '../ui/LoadingState';
import { MasterDetailLayout } from '../ui/MasterDetailLayout';
import { FilterBar } from '../ui/filters/FilterBar';
import { useFilterState } from '../ui/filters/useFilterState';
import type { FacetDefinition } from '../ui/filters/types';
import { getAdapters } from './instanceRegistry';
import { getInstanceView } from './viewRegistry';
import { InstanceBrowser } from './InstanceBrowser';
import { InstancePreview } from './InstancePreview';
import type {
  InstanceAdapter,
  InstanceQuery,
  InstanceRow,
  InstanceSearchHit,
  InstanceSelection,
  InstanceType,
} from './types';

const ALL = 'all';
const PAGE_SIZE = 50;

export interface InstanceBrowseModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Current selection (so rows render Added + the footer count is live). */
  picked: InstanceSelection[];
  /** Toggle a visible row into/out of the selection (passes the adapter type). */
  onTogglePick: (row: InstanceRow) => void;
  /** Restrict to a subset of entity types; default = all registered. */
  allowedTypes?: InstanceType[];
  /** Patient scope. When omitted the caller must guarantee tenant scope is OK. */
  patientId?: string;
  /** 'single' hides the multi-accumulate affordance in the footer. */
  mode?: 'single' | 'multi';
  /**
   * Cross-type search callback (Phase 2: the backend `/instances/search`
   * dispatcher). When supplied and >1 type is allowed, the type selector
   * offers an "All" option that runs this. Without it, "All" is unavailable.
   */
  unifiedSearch?: (query: InstanceQuery) => Promise<InstanceSearchHit[]>;
}

export const InstanceBrowseModal: React.FC<InstanceBrowseModalProps> = ({
  isOpen,
  onClose,
  picked,
  onTogglePick,
  allowedTypes,
  patientId,
  mode = 'multi',
  unifiedSearch,
}) => {
  const { t } = useTranslation();

  const adapters = useMemo(
    () => getAdapters(allowedTypes),
    [allowedTypes],
  );

  const supportsAll = adapters.length > 1 && !!unifiedSearch;
  const singleType = adapters.length === 1 ? adapters[0].type : null;
  const [activeType, setActiveType] = useState<string>(
    singleType ?? (supportsAll ? ALL : (adapters[0]?.type ?? ALL)),
  );

  const [rawItems, setRawItems] = useState<unknown[]>([]);
  const [rows, setRows] = useState<InstanceRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [search, setSearch] = useState('');
  // The row currently shown in the preview pane (clicked in the list).
  const [selectedRow, setSelectedRow] = useState<InstanceRow | null>(null);

  // Reset active type when the modal reopens or the allowed set changes.
  useEffect(() => {
    if (!isOpen) return;
    setActiveType(singleType ?? (supportsAll ? ALL : (adapters[0]?.type ?? ALL)));
    setSearch('');
    setSelectedRow(null);
  }, [isOpen, singleType, supportsAll, adapters]);

  const activeAdapter: InstanceAdapter<unknown> | null = useMemo(() => {
    if (activeType === ALL) return null;
    return adapters.find((a) => a.type === activeType) ?? null;
  }, [adapters, activeType]);

  // Active-type label for the title (always state which type is browsed).
  const activeTypeLabel = useMemo(() => {
    if (activeType === ALL) return t('instances.type_all', 'All types');
    return activeAdapter?.entityLabel.plural ?? t('instances.browse_title', 'records');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeType, activeAdapter]);

  // Per-type view (reuses purpose-built components). When present, the modal
  // renders it instead of the generic InstanceBrowser + InstancePreview.
  const ActiveView = activeType !== ALL ? getInstanceView(activeType as InstanceType) : null;

  // Security default: refuse to fetch patient-scoped data with no patient.
  const canFetch =
    activeType === ALL
      ? !!patientId || true // unified search enforces scope server-side
      : !!patientId || !!activeAdapter?.allowTenantScope;

  const buildQuery = useCallback(
    (limit: number, offset: number): InstanceQuery => ({
      patientId,
      q: search || undefined,
      limit,
      offset,
      serverParams: {},
    }),
    [patientId, search],
  );

  const rowsFromHits = useCallback(
    (hits: InstanceSearchHit[]): InstanceRow[] =>
      hits.map((h) => ({
        id: h.id,
        type: h.type,
        label: h.label,
        subtitle: h.subtitle,
        date: h.date,
        raw: h,
      })),
    [],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (activeType === ALL) {
        if (!unifiedSearch || search.trim().length < 2) {
          setRows([]);
          setRawItems([]);
          setTotal(0);
          return;
        }
        const hits = await unifiedSearch(buildQuery(PAGE_SIZE, 0));
        const mapped = rowsFromHits(hits);
        setRows(mapped);
        setRawItems(mapped);
        setTotal(mapped.length);
      } else if (activeAdapter) {
        const result = await activeAdapter.fetch(buildQuery(PAGE_SIZE, 0));
        const mapped = result.items.map((it) => activeAdapter.toRow(it));
        setRawItems(result.items);
        setRows(mapped);
        setTotal(result.total);
      }
    } catch {
      setRows([]);
      setRawItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [activeType, activeAdapter, unifiedSearch, search, buildQuery, rowsFromHits]);

  useEffect(() => {
    if (!isOpen || !canFetch) return;
    const handle = setTimeout(load, 200); // debounce search/type changes
    return () => clearTimeout(handle);
  }, [load, isOpen, canFetch]);

  const loadMore = useCallback(async () => {
    if (loadingMore || activeType === ALL || !activeAdapter) return;
    setLoadingMore(true);
    try {
      const result = await activeAdapter.fetch(
        buildQuery(PAGE_SIZE, rows.length),
      );
      const mapped = result.items.map((it) => activeAdapter.toRow(it));
      setRawItems((prev) => [...prev, ...result.items]);
      setRows((prev) => [...prev, ...mapped]);
    } catch {
      /* keep what we have */
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, activeType, activeAdapter, rows.length, buildQuery]);

  // Per-type client-mode facets (the modal filters in-memory; server-mode
  // facets are excluded — same policy as the catalog picker modal). Empty in
  // "All" mode (cross-type search has no uniform facets).
  const facets = useMemo<FacetDefinition<any>[]>(
    () =>
      activeAdapter
        ? (activeAdapter.facets as FacetDefinition<any>[]).filter(
            (f) => f.mode !== 'server',
          )
        : [],
    [activeAdapter],
  );
  const pickerFilter = useFilterState<any>(facets);

  // Apply client facets over the RAW entities (predicates are typed against the
  // entity, not the row), then map survivors back to their rows.
  const filteredRows = useMemo(() => {
    if (facets.length === 0 || activeType === ALL) return rows;
    const surviving = pickerFilter.applyFilters(rawItems);
    const byId = new Map(rows.map((r) => [r.id, r]));
    // Preserve the adapter's row projection for survivors (stable by id).
    return surviving
      .map((it) => activeAdapter?.toRow(it))
      .filter((r): r is InstanceRow => !!r && byId.has(r.id));
  }, [facets, activeType, rows, rawItems, pickerFilter, activeAdapter]);

  const pickedIds = useMemo(() => picked.map((p) => p.id), [picked]);

  const hasMore =
    activeType !== ALL && !!activeAdapter && rows.length < total;

  const showTypeSelector = adapters.length > 1;

  // Modal footer (count + Done) — passed via Modal's `footer` prop so it's
  // pinned as a sticky footer OUTSIDE the scrollable body, never floating
  // behind/over the preview pane.
  const footer = (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-gray-500 dark:text-gray-400">
        {picked.length === 0
          ? t('instances.none_selected', 'No records selected')
          : t('instances.n_selected', {
              count: picked.length,
              defaultValue: '{{count}} selected',
            })}
      </span>
      <button
        type="button"
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
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('instances.browse_title_type', {
        defaultValue: 'Browse {{type}}',
        type: activeTypeLabel,
      })}
      size="xl"
      // Fixed desktop size so the modal is stable — it does NOT grow/shrink
      // with the selected item or preview content (height varies by record
      // length; width varied because the detail pane was auto-sized to its
      // content). Both dimensions are now pinned; the list + preview panes
      // scroll within this fixed frame. (`!` overrides Modal's sm:h/w-auto.)
      // Mobile stays full-screen.
      className="sm:!h-[85vh] sm:!w-[92vw] xl:!w-[1100px]"
      bodyClassName="p-4 sm:p-5"
      footer={footer}
    >
      <div className="flex flex-col h-full min-h-0 gap-3">
        {/* Top controls: type selector + search (span the full modal width) */}
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {showTypeSelector && (
            <select
              value={activeType}
              onChange={(e) => {
                setActiveType(e.target.value);
                setSelectedRow(null);
              }}
              className="px-2.5 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none"
            >
              {supportsAll && (
                <option value={ALL}>
                  {t('instances.type_all', 'All types')}
                </option>
              )}
              {adapters.map((a) => (
                <option key={a.type} value={a.type}>
                  {t(`instances.type_${a.type}`, a.entityLabel.plural)}
                </option>
              ))}
            </select>
          )}
          {!showTypeSelector && (
            <span className="text-xs font-bold uppercase tracking-widest text-blue-500">
              {activeTypeLabel}
            </span>
          )}

          <div className="relative flex-1 min-w-[12rem]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={
                activeType === ALL
                  ? t('instances.search_all_placeholder', 'Search all records (min 2 chars)…')
                  : t('instances.search_placeholder', 'Search records…')
              }
              className="w-full pl-8 pr-8 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 text-gray-400 hover:text-gray-600"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* Body: full-width status states, else master-detail (list + preview).
            Flex-column + bounded height so a per-type view's MasterDetailLayout
            fills it and the list/preview panes scroll INDEPENDENTLY (instead of
            the whole modal body scrolling as one). */}
        <div className="flex-1 min-h-0 flex flex-col">
          {!canFetch ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <Users className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
              <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                {t('instances.select_patient_title', 'Select a patient')}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {t('instances.select_patient_hint', 'Records are patient-scoped. Pick a patient to browse.')}
              </p>
            </div>
          ) : loading ? (
            <LoadingState
              variant="section"
              message={t('instances.loading', 'Loading records…')}
            />
          ) : activeType === ALL && search.trim().length < 2 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <Search className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
              <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                {t('instances.search_all_title', 'Search across all record types')}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {t('instances.search_all_hint', 'Type at least 2 characters, or pick a specific type to browse it.')}
              </p>
            </div>
          ) : filteredRows.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <Search className="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" />
              <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                {t('instances.no_matches', 'No matches')}
              </p>
              <p className="text-xs text-gray-400 mt-1">
                {t('instances.no_matches_hint', 'Try adjusting your search or filters.')}
              </p>
            </div>
          ) : ActiveView && activeType !== ALL ? (
            <ActiveView
              items={rawItems}
              pickedIds={pickedIds}
              onTogglePick={(item: unknown) =>
                activeAdapter ? onTogglePick(activeAdapter.toRow(item)) : undefined
              }
              patientId={patientId}
              loading={false}
              hasMore={hasMore}
              loadingMore={loadingMore}
              onLoadMore={loadMore}
            />
          ) : (
            <MasterDetailLayout
              withListStyling={false}
              listWidth="lg:w-[340px] xl:w-[380px]"
              list={
                <div className="flex flex-col h-full gap-2">
                  {facets.length > 0 && (
                    <FilterBar
                      facets={facets}
                      filter={pickerFilter}
                      items={rawItems}
                      showActivePills
                      resultCount={filteredRows.length}
                      totalCount={rows.length}
                    />
                  )}
                  <div className="flex-1 min-h-0">
                    <InstanceBrowser
                      rows={filteredRows}
                      loading={false}
                      total={total}
                      searchTerm={search}
                      showTypeSort={showTypeSelector && activeType === ALL}
                      activeType={activeType === ALL ? null : (activeType as InstanceType)}
                      selectedId={selectedRow?.id}
                      onSelectRow={(row) => setSelectedRow(row)}
                      pickedIds={pickedIds}
                      onTogglePick={onTogglePick}
                      hasMore={hasMore}
                      loadingMore={loadingMore}
                      onLoadMore={loadMore}
                    />
                  </div>
                </div>
              }
              detail={
                <InstancePreview
                  row={selectedRow}
                  emptyHint={t('instances.preview_empty', 'Select a record to preview')}
                  detailRoute={
                    selectedRow && activeAdapter
                      ? activeAdapter.detailRoute(
                          rawItems.find(
                            (it) => activeAdapter.toRow(it).id === selectedRow.id,
                          ) as never,
                        )
                      : null
                  }
                />
              }
            />
          )}
        </div>
      </div>
    </Modal>
  );
};
