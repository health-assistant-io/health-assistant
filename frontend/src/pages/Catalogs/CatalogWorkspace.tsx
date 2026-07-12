/**
 * Unified Catalogs workspace — one screen for every catalog.
 *
 * Two-pane master-detail rework (Phase 3.5):
 *   Toolbar band  — CatalogTypeSelect (searchable) + scope + class chips +
 *                   view toggle + New. Replaces the old left rail so the list
 *                   + preview get the full remaining width. Item search is the
 *                   global page-search (header SearchLauncher) — this page opts
 *                   in via setIsPageSearchSupported and filters `items`
 *                   in-memory against `pageSearchTerm` (no local search box).
 *   List pane     — scrollable browser of items (selecting sets ?item=).
 *   Preview pane  — tabs (Info | Relations | custom) for the selected item,
 *                   rendered beside the list on desktop and as a full-screen
 *                   popup on small displays.
 *
 * Registry-driven: the catalog types come from `GET /catalogs`. The "concept"
 * type links out to the dedicated Taxonomy Manager.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Database, X, Save, ArrowLeft, Info as InfoIcon, Edit3, History as HistoryIcon, ExternalLink, Trash2, RotateCcw, List as ListIcon, GitBranch } from 'lucide-react';
import PageHeader from '../../components/ui/PageHeader';
import { PageContainer } from '../../components/ui/PageContainer';
import { LoadingState } from '../../components/ui/LoadingState';
import { Portal } from '../../components/ui/Portal';
import { CatalogToolbar } from '../../components/catalog/CatalogToolbar';
import { CatalogBrowser } from '../../components/catalog/CatalogBrowser';
import { CatalogItemInfo } from '../../components/catalog/CatalogItemInfo';
import { CatalogRelationsGraph } from '../../components/catalog/CatalogRelationsGraph';
import { CatalogRelationsIndex } from '../../components/catalog/CatalogRelationsIndex';
import { CatalogRelationsEditor } from '../../components/catalog/CatalogRelationsEditor';
import { CatalogAuditHistoryModal } from '../../components/catalog/CatalogAuditHistoryModal';
import { getCatalogForm } from '../../components/catalog/forms/catalogForms';
import { getWriteTarget, buildWritePayload } from '../../components/catalog/writeTarget';
import { CatalogOntologyGraph } from '../../components/catalog/CatalogOntologyGraph';
import { getCustomTabs } from '../../components/catalog/tabs/catalogTabs';
import { BiomarkerMigrationWatcher } from '../../components/biomarkers/BiomarkerMigrationWatcher';
import { ScopeBadge } from '../../components/catalog/ScopeBadge';
import {
  listCatalogTypes,
  listCatalogItems,
  createCatalogItem,
  updateCatalogItem,
  deleteCatalogItem,
} from '../../services/catalogService';
import type { CatalogItem, CatalogTypeMeta } from '../../types/catalog';
import { CONCEPT_KIND_LABELS } from '../../types/concept';
import { useAuthStore } from '../../store/slices/authSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { useMasterDetail } from '../../hooks/useMasterDetail';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { domainRouteForType } from '../../utils/domainRoute';

const INFO = 'info';
const RELATIONS = 'relations';
const MOBILE_BREAKPOINT = 1024;

export const CatalogWorkspace: React.FC = () => {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { isLargeScreen } = useMasterDetail({ breakpoint: MOBILE_BREAKPOINT });

  const [types, setTypes] = useState<CatalogTypeMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<string>(INFO);
  const [viewMode, setViewMode] = useState<'list' | 'card'>('list');
  // Graph mode is URL-driven (?view=graph) so it's routable, deep-linkable,
  // and survives refresh/back navigation.
  const graphMode: 'list' | 'graph' = searchParams.get('view') === 'graph' ? 'graph' : 'list';
  const setGraphMode = (mode: 'list' | 'graph') => {
    setSearchParams(
      (prev) => {
        if (mode === 'graph') prev.set('view', 'graph');
        else prev.delete('view');
        return prev;
      },
      { replace: true },
    );
  };

  // Items (owned here so the browser, preview Info + Relations tabs share).
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [itemsLoading, setItemsLoading] = useState(false);

  // Anatomy-only: taxonomy-class filter chips (organ/region/system/…).
  const [classFilter, setClassFilter] = useState<string>('');
  const [anatomyClasses, setAnatomyClasses] = useState<
    { slug: string; name: string }[]
  >([]);

  // Edit + audit modal state (owned here so the browser + preview can open it).
  const [editing, setEditing] = useState<CatalogItem | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [saving, setSaving] = useState(false);
  // Bumped after a save so the Relations graph refetches (it fetches once per
  // item selection and would otherwise stay stale until re-selected).
  const [relationsRevision, setRelationsRevision] = useState(0);
  const [historyItem, setHistoryItem] = useState<{ id: string; name: string } | null>(null);

  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const pageSearchTerm = useUIStore((s) => s.pageSearchTerm);
  const setPageSearchTerm = useUIStore((s) => s.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore((s) => s.setIsPageSearchSupported);
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
  const currentUserRole = useAuthStore((s) => s.user?.role ?? null);

  /** Left-rail scope filter: '' (all) | 'system' | 'tenant' | 'mine'. */
  const [scopeFilter, setScopeFilter] = useState<string>('');

  const activeType = searchParams.get('type') || '';
  // Concepts are a curated taxonomy — USER role cannot create/edit (AD-9).
  // Other catalog types allow USER to create user-scope items.
  const canCreate = activeType === 'concept'
    ? currentUserRole !== 'USER' && currentUserRole !== null
    : true;
  const itemId = searchParams.get('item') || '';

  // Register this page as a page-search provider (the header SearchLauncher
  // then filters the loaded items in-memory via `pageSearchTerm`). Cleared on
  // unmount so other pages aren't affected.
  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setPageSearchTerm('');
    };
  }, [setIsPageSearchSupported, setPageSearchTerm]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const resp = await listCatalogTypes();
        if (cancelled) return;
        setTypes(resp.types);
        if (!activeType && resp.types.length > 0) {
          setSearchParams({ type: resp.types[0].type }, { replace: true });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const active = types.find((tg) => tg.type === activeType) || null;
  const customTabs = useMemo(() => (active ? getCustomTabs(active.type) : []), [active]);

  const PAGE_SIZE = 50;
  const isMineScope = scopeFilter === 'mine';

  const load = useCallback(async () => {
    if (!activeType) return;
    setItemsLoading(true);
    try {
      // Concepts filter by ``kind`` (the tag join); other catalogs by ``class``
      // (the class_concept_id FK). The toolbar chips feed the same state.
      const kindOrClass =
        activeType === 'concept'
          ? { kind: classFilter || undefined }
          : { class: classFilter || undefined };
      const resp = await listCatalogItems(activeType, {
        // 'mine' is a client-side ownership filter over the user-scope set, so
        // fetch it all in one shot (no pagination — offset would be wrong).
        scope: isMineScope ? 'user' : scopeFilter || undefined,
        ...kindOrClass,
        include: 'relations',
        limit: isMineScope ? 500 : PAGE_SIZE,
        offset: 0,
      });
      setItems(resp.items);
      setTotal(resp.total);
    } catch {
      setItems([]);
      setTotal(0);
    } finally {
      setItemsLoading(false);
    }
  }, [activeType, scopeFilter, classFilter, isMineScope]);

  const [loadingMore, setLoadingMore] = useState(false);
  const loadMore = useCallback(async () => {
    if (!activeType || loadingMore || isMineScope) return;
    setLoadingMore(true);
    try {
      const kindOrClass =
        activeType === 'concept'
          ? { kind: classFilter || undefined }
          : { class: classFilter || undefined };
      const resp = await listCatalogItems(activeType, {
        scope: scopeFilter || undefined,
        ...kindOrClass,
        include: 'relations',
        limit: PAGE_SIZE,
        offset: items.length,
      });
      setItems((prev) => [...prev, ...resp.items]);
    } catch {
      /* ignore — keep what we have */
    } finally {
      setLoadingMore(false);
    }
  }, [activeType, scopeFilter, classFilter, items.length, loadingMore, isMineScope]);

  useEffect(() => {
    load();
  }, [load]);

  /** In-memory filters: 'mine' ownership + page-search, over the loaded items. */
  const filteredItems = useMemo(() => {
    let result = items;
    if (isMineScope) {
      result = result.filter((it) =>
        it.created_by && currentUserId ? it.created_by === currentUserId : false,
      );
    }
    const q = pageSearchTerm.trim().toLowerCase();
    if (q) {
      result = result.filter((it) => {
        const name = (it.name || it.slug || it.id || '').toString().toLowerCase();
        const desc = it.description ? String(it.description).toLowerCase() : '';
        return name.includes(q) || desc.includes(q);
      });
    }
    return result;
  }, [items, isMineScope, pageSearchTerm, currentUserId]);

  /** More pages available on the backend (only meaningful for non-'mine'). */
  const hasMore = !isMineScope && items.length < total;

  // Reset selection + tab + class filter when the catalog type changes.
  useEffect(() => {
    setTab(INFO);
    setClassFilter('');
  }, [activeType]);

  // Anatomy-only: load the anatomy_class concepts for the filter chips.
  // Concept-only: the kind chips are the static ConceptKind enum (no fetch).
  useEffect(() => {
    if (activeType === 'concept') {
      // Kind chips for concepts come from the static enum labels.
      setAnatomyClasses(
        Object.entries(CONCEPT_KIND_LABELS).map(([slug, name]) => ({
          slug,
          name,
        })),
      );
      return;
    }
    if (activeType !== 'anatomy') {
      setAnatomyClasses([]);
      return;
    }
    let cancelled = false;
    listCatalogItems('concept', { kind: 'anatomy_class', limit: 50 })
      .then((resp) => {
        if (cancelled) return;
        setAnatomyClasses(
          resp.items.map((it) => ({
            slug: String(it.slug),
            name: String(it.name ?? it.slug),
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setAnatomyClasses([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeType]);

  const selectType = (type: string) => {
    setSearchParams({ type }, { replace: true });
  };

  const selectItem = (id: string, type?: string | null) => {
    setSearchParams(
      (prev) => {
        // When a cross-catalog node carries its own type (e.g. clicking a
        // biomarker node from the concept graph), switch the catalog type so
        // the item resolves under the correct browser.
        if (type && type !== activeType) prev.set('type', type);
        if (id) prev.set('item', id);
        else prev.delete('item');
        return prev;
      },
      { replace: true },
    );
  };

  const domainRoute = (item: CatalogItem): string | null =>
    domainRouteForType(
      activeType,
      String(item.id),
      item.slug ? String(item.slug) : undefined,
    );

  const performSave = async () => {
    if (!editing || saving) return;
    setSaving(true);
    try {
      const writeTarget = getWriteTarget(activeType);
      if (isNew) {
        if (writeTarget) {
          await writeTarget.create(buildWritePayload(activeType, editing, 'create'));
        } else {
          await createCatalogItem(activeType, editing);
        }
      } else {
        const id = String(editing.id);
        if (writeTarget) {
          await writeTarget.update(id, buildWritePayload(activeType, editing, 'edit'));
          // No returned row to patch locally for concept writes; a full
          // load() below reconciles the list.
        } else {
          const saved = await updateCatalogItem(activeType, id, editing);
          // Patch the returned row into the local list so the Info preview
          // reflects the edit immediately (the response is the updated item).
          setItems((prev) =>
            prev.map((it) =>
              String(it.id) === String(saved.id) ? { ...it, ...saved } : it,
            ),
          );
        }
      }
      toast.success(t('common.save_success', 'Saved'));
      setEditing(null);
      setIsNew(false);
      // Nudge the Relations graph to refetch (edges may have changed via the
      // inline editor, and the graph wouldn't reload otherwise).
      setRelationsRevision((n) => n + 1);
      // Full refresh reconciles relation_count/breakdown + pagination.
      load();
    } catch (e: any) {
      // Previously this was `catch { /* toast */ }` — a silent swallow that
      // hid backend errors (e.g. the is_custom 500), so edits appeared to do
      // nothing. Surface the backend detail instead.
      const detail = e?.response?.data?.detail;
      toast.error(
        typeof detail === 'string'
          ? detail
          : t('common.save_failed', 'Could not save'),
      );
    } finally {
      setSaving(false);
    }
  };

  const handleSave = () => {
    if (!editing || saving) return;

    // Flip of is_telemetry on an existing biomarker triggers a FHIR↔
    // TimescaleDB data migration on the backend — warn before committing.
    if (!isNew && activeType === 'biomarker' && editing.id) {
      const original = items.find(
        (it) => String(it.id) === String(editing.id),
      );
      const originalTelemetry = Boolean(original?.is_telemetry);
      const draftTelemetry = Boolean(editing.is_telemetry);
      if (originalTelemetry !== draftTelemetry) {
        showConfirmation({
          title: t('biomarkers.migration_confirm_title', 'Migrate Telemetry Data?'),
          message: t(
            'biomarkers.migration_confirm_message',
            "Warning: Changing this biomarker's telemetry type will migrate all existing historical data between databases. This could take a while for large datasets. Are you sure you want to continue?",
          ),
          confirmLabel: t('biomarkers.migration_confirm_button', 'Yes, Migrate Data'),
          cancelLabel: t('common.cancel', 'Cancel'),
          confirmVariant: 'danger',
          onConfirm: performSave,
        });
        return;
      }
    }

    performSave();
  };

  const handleDelete = (item: CatalogItem) => {
    showConfirmation({
      title: t('common.delete'),
      message: t('catalogs.delete_confirm', { name: String(item.name ?? item.id) }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          const writeTarget = getWriteTarget(activeType);
          if (writeTarget) {
            await writeTarget.remove(String(item.id));
          } else {
            await deleteCatalogItem(activeType, String(item.id));
          }
          toast.success(t('common.delete_success', 'Deleted'));
          load();
        } catch (e: any) {
          const detail = e?.response?.data?.detail;
          toast.error(
            typeof detail === 'string'
              ? detail
              : t('common.delete_failed', 'Could not delete'),
          );
        }
      },
    });
  };

  const handleRestore = async (item: CatalogItem) => {
    try {
      const writeTarget = getWriteTarget(activeType);
      if (writeTarget?.restore) {
        await writeTarget.restore(String(item.id));
        toast.success(t('catalogs.restore_success', 'Restored'));
        load();
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      toast.error(
        typeof detail === 'string'
          ? detail
          : t('catalogs.restore_failed', 'Could not restore'),
      );
    }
  };

  const labelField = (item: CatalogItem): string =>
    String(item.name || item.slug || item.id || 'Unnamed');

  const selectedItem = useMemo(
    () => (itemId ? items.find((it) => String(it.id) === itemId) ?? null : null),
    [items, itemId],
  );

  const allTabs = [{ id: INFO, label: t('catalogs.tab_info', 'Info') }, { id: RELATIONS, label: t('catalogs.tab_relations', 'Relations') }, ...customTabs.map((ct) => ({ id: ct.id, label: t(ct.labelKey, ct.labelFallback) }))];

  /** The preview body (shared by desktop detail pane + mobile popup). */
  const renderPreviewBody = () => {
    if (tab === INFO) {
      return (
        <CatalogItemInfo
          item={selectedItem}
          total={total}
          hideHeader
        />
      );
    }
    if (tab === RELATIONS) {
      return itemId && selectedItem ? (
        <CatalogRelationsGraph
          key={itemId}
          catalogType={active!.type}
          itemId={itemId}
          itemLabel={labelField(selectedItem)}
          refreshKey={relationsRevision}
        />
      ) : (
        <CatalogRelationsIndex items={items} onSelectItem={selectItem} />
      );
    }
    const ct = customTabs.find((c) => c.id === tab);
    if (!ct || !active) return null;
    const TabComponent = ct.Component;
    return <TabComponent typeMeta={active} />;
  };

  /** Tab strip for the preview pane. */
  const renderTabs = () => (
    <div className="flex flex-wrap gap-1 border-b border-gray-200 dark:border-gray-700">
      {allTabs.map((tb) => (
        <button
          key={tb.id}
          onClick={() => setTab(tb.id)}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
            tab === tb.id
              ? 'border-blue-500 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          {tb.label}
        </button>
      ))}
    </div>
  );

  /** The preview pane (desktop detail column). */
  const renderPreviewPane = (extraClass = '') => (
    <div
      className={`flex flex-col w-full h-full min-h-0 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden ${extraClass}`}
    >
      <div className="shrink-0 px-4 pt-3">
        {/* Selected-item context line + actions */}
        <div className="flex items-center gap-2 mb-2 min-w-0">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {selectedItem ? (
              <>
                <span className="text-sm font-semibold truncate">
                  {labelField(selectedItem)}
                </span>
                <ScopeBadge
                  scope={selectedItem.scope}
                  created_by={selectedItem.created_by}
                  currentUserId={currentUserId}
                />
              </>
            ) : (
              <span className="text-xs text-gray-400 flex items-center gap-1.5">
                <InfoIcon className="w-3.5 h-3.5" />
                {t('catalogs.preview_none', 'Select an item to preview')}
              </span>
            )}
          </div>
          {selectedItem && (
            <div className="flex items-center gap-0.5 shrink-0">
              <button
                onClick={() => {
                  setEditing({ ...selectedItem });
                  setIsNew(false);
                }}
                title={t('catalogs.edit', 'Edit')}
                className="p-1.5 text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md"
              >
                <Edit3 className="w-4 h-4" />
              </button>
              <button
                onClick={() =>
                  setHistoryItem({ id: String(selectedItem.id), name: labelField(selectedItem) })
                }
                title={t('catalogs.audit_history_title', 'History')}
                className="p-1.5 text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md"
              >
                <HistoryIcon className="w-4 h-4" />
              </button>
              <button
                onClick={() => handleDelete(selectedItem)}
                title={t('common.delete', 'Delete')}
                className="p-1.5 text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md"
              >
                <Trash2 className="w-4 h-4" />
              </button>
              {selectedItem.status === 'retired' && getWriteTarget(activeType)?.restore && (
                <button
                  onClick={() => handleRestore(selectedItem)}
                  title={t('catalogs.restore', 'Restore')}
                  className="p-1.5 text-gray-500 dark:text-gray-400 hover:text-green-600 dark:hover:text-green-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md"
                >
                  <RotateCcw className="w-4 h-4" />
                </button>
              )}
              {domainRoute(selectedItem) && (
                <a
                  href={domainRoute(selectedItem)!}
                  title={t('catalogs.open_in_domain', 'Open in domain view')}
                  className="p-1.5 text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md"
                >
                  <ExternalLink className="w-4 h-4" />
                </a>
              )}
            </div>
          )}
        </div>
        {renderTabs()}
      </div>
      <div className="flex-1 overflow-y-auto min-h-0 p-4 custom-scrollbar">
        {activeType === 'biomarker' && selectedItem && (
          <BiomarkerMigrationWatcher
            biomarkerId={String(selectedItem.id)}
            refreshKey={relationsRevision}
            seed={
              (selectedItem as Record<string, any>).meta_data as
                | {
                    migration_status?: string;
                    migration_progress?: number;
                    migration_error?: string;
                  }
                | undefined
            }
            onBiomarkerUpdated={(updated) =>
              setItems((prev) =>
                prev.map((it) =>
                  String(it.id) === String(updated.id)
                    ? { ...it, ...updated }
                    : it,
                ),
              )
            }
          />
        )}
        {renderPreviewBody()}
      </div>
    </div>
  );

  /** Mobile full-screen preview popup (Portal overlay). */
  const renderMobilePopup = () => (
    <Portal>
      <div className="fixed inset-0 z-modal flex flex-col bg-white dark:bg-gray-900 animate-in fade-in slide-in-from-bottom-4 duration-200">
        <div className="shrink-0 flex items-center gap-2 px-3 py-3 border-b border-gray-200 dark:border-gray-700">
          <button
            onClick={() => selectItem('')}
            className="flex items-center gap-1.5 px-2 py-1.5 text-sm font-medium rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            <ArrowLeft className="w-4 h-4" />
            {t('catalogs.back_to_list', 'Back to list')}
          </button>
          <span className="text-sm font-semibold truncate flex-1">
            {selectedItem ? labelField(selectedItem) : ''}
          </span>
          {selectedItem && (
            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={() => {
                  setEditing({ ...selectedItem });
                  setIsNew(false);
                }}
                className="p-1.5 text-gray-500 hover:text-blue-500"
                title={t('catalogs.edit', 'Edit')}
              >
                <Edit3 className="w-4 h-4" />
              </button>
              <button
                onClick={() =>
                  setHistoryItem({ id: String(selectedItem.id), name: labelField(selectedItem) })
                }
                className="p-1.5 text-gray-500 hover:text-indigo-500"
                title={t('catalogs.audit_history_title', 'History')}
              >
                <HistoryIcon className="w-4 h-4" />
              </button>
              <button
                onClick={() => handleDelete(selectedItem)}
                className="p-1.5 text-gray-500 hover:text-red-500"
                title={t('common.delete', 'Delete')}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
        <div className="shrink-0 px-3 pt-2">{renderTabs()}</div>
        <div className="flex-1 overflow-y-auto min-h-0 p-3">
          {activeType === 'biomarker' && selectedItem && (
            <BiomarkerMigrationWatcher
              biomarkerId={String(selectedItem.id)}
              refreshKey={relationsRevision}
              seed={
                (selectedItem as Record<string, any>).meta_data as
                  | {
                      migration_status?: string;
                      migration_progress?: number;
                      migration_error?: string;
                    }
                  | undefined
              }
              onBiomarkerUpdated={(updated) =>
                setItems((prev) =>
                  prev.map((it) =>
                    String(it.id) === String(updated.id)
                      ? { ...it, ...updated }
                      : it,
                  ),
                )
              }
            />
          )}
          {renderPreviewBody()}
        </div>
      </div>
    </Portal>
  );

  return (
    <PageContainer>
      <PageHeader
        title={t('catalogs.title', 'Catalogs')}
        subtitle={t('catalogs.subtitle', 'Browse and manage every clinical reference catalog')}
        icon={<Database className="w-8 h-8" />}
        breadcrumbs={[{ label: t('catalogs.title', 'Catalogs') }]}
      />

      {loading ? (
        <LoadingState variant="section" message={t('catalogs.loading', 'Loading catalogs…')} />
      ) : (
        <>
          {/* Consolidated toolbar band */}
          <div className="shrink-0">
            <CatalogToolbar
              types={types}
              activeType={activeType}
              onSelectType={selectType}
              scopeFilter={scopeFilter}
              onScopeChange={setScopeFilter}
              anatomyClasses={anatomyClasses}
              classFilter={classFilter}
              onClassChange={setClassFilter}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              onNew={canCreate ? () => {
                setEditing({ name: '' });
                setIsNew(true);
              } : undefined}
              canExportSeeds={currentUserRole === 'SYSTEM_ADMIN'}
            />
          </div>

          {/* List | Graph exploration toggle (all catalog types) */}
          {active && (
            <div className="shrink-0 flex items-center gap-2 py-1">
              <div className="flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
                <button
                  onClick={() => setGraphMode('list')}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium ${
                    graphMode === 'list'
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
                  }`}
                >
                  <ListIcon className="w-3.5 h-3.5" /> {t('catalogs.view_list', 'List')}
                </button>
                <button
                  onClick={() => setGraphMode('graph')}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium ${
                    graphMode === 'graph'
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
                  }`}
                >
                  <GitBranch className="w-3.5 h-3.5" /> {t('catalogs.view_graph', 'Graph')}
                </button>
              </div>
            </div>
          )}

          {active ? (
            graphMode === 'graph' ? (
              /* Whole cross-catalog ontology graph view */
              <div className="flex-1 min-h-0">
                <CatalogOntologyGraph
                  refreshKey={relationsRevision}
                  onFocusNode={(node) => {
                    selectItem(node.id, node.type ?? undefined);
                  }}
                />
              </div>
            ) : (
            <div className="flex gap-4 flex-1 min-h-0">
              {/* List pane */}
              <div className="w-full lg:w-[42%] xl:w-[38%] shrink-0 flex flex-col min-h-0">
                  <CatalogBrowser
                    key={active.type}
                    items={filteredItems}
                    loading={itemsLoading}
                    total={isMineScope ? filteredItems.length : total}
                    viewMode={viewMode}
                    searchTerm={pageSearchTerm}
                    selectedItemId={itemId || undefined}
                    onSelectItem={selectItem}
                    domainRoute={domainRoute}
                    hasMore={hasMore}
                    onLoadMore={loadMore}
                    loadingMore={loadingMore}
                    onScopeClick={(s) => setScopeFilter((prev) => (prev === s ? '' : s))}
                    onClassClick={(slug) => {
                      const set = new Set(classFilter ? classFilter.split(',') : []);
                      if (set.has(slug)) set.delete(slug);
                      else set.add(slug);
                      setClassFilter([...set].join(','));
                    }}
                    activeScope={scopeFilter}
                    activeClasses={classFilter ? classFilter.split(',') : []}
                    hasActiveFilters={!!pageSearchTerm || !!scopeFilter || !!classFilter}
                    onClearFilters={() => {
                      setPageSearchTerm('');
                      setScopeFilter('');
                      setClassFilter('');
                    }}
                  />
                </div>

                {/* Preview pane (desktop only) */}
                {isLargeScreen && (
                  <div className="hidden lg:flex flex-1 min-h-0">
                    {renderPreviewPane()}
                  </div>
                )}
            </div>
            )
          ) : (
            <p className="text-sm text-gray-500">{t('catalogs.select_type', 'Select a catalog type.')}</p>
          )}
        </>
      )}

      {/* Mobile full-screen preview popup */}
      {!isLargeScreen && selectedItem && active && renderMobilePopup()}

      {/* Audit history modal */}
      <CatalogAuditHistoryModal
        type={activeType}
        itemId={historyItem?.id ?? null}
        itemName={historyItem?.name}
        onClose={() => setHistoryItem(null)}
      />

      {/* Edit modal */}
      {editing && active && (() => {
        const ItemForm = getCatalogForm(active.type);
        const editingId = editing.id ? String(editing.id) : null;
        return (
          <div className="fixed inset-0 z-modal flex items-center justify-center bg-black/40 p-4">
            <div className="w-full max-w-2xl max-h-[90vh] flex flex-col rounded-xl bg-white dark:bg-gray-800 shadow-xl overflow-hidden">
              <div className="flex items-center justify-between shrink-0 px-6 pt-6 pb-4 border-b border-gray-100 dark:border-gray-700">
                <h3 className="text-lg font-semibold">
                  {isNew ? t('catalogs.create', 'Create') : t('catalogs.edit', 'Edit')} {active.type}
                </h3>
                <button
                  onClick={() => {
                    setEditing(null);
                    setIsNew(false);
                  }}
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4">
                <ItemForm
                  typeMeta={active}
                  values={editing}
                  mode={isNew ? 'create' : 'edit'}
                  onChange={(patch) => setEditing({ ...editing, ...patch })}
                />

                {!isNew && editingId && (
                  <div className="border-t border-gray-100 dark:border-gray-700 pt-4">
                    <CatalogRelationsEditor typeMeta={active} itemId={editingId} />
                  </div>
                )}
                {isNew && (
                  <p className="text-xs text-gray-400 border-t border-gray-100 dark:border-gray-700 pt-3">
                    {t('catalogs.relations_save_first', 'Save the item first, then you can add relations.')}
                  </p>
                )}
              </div>

              <div className="flex justify-end gap-2 shrink-0 px-6 pb-6 pt-4 border-t border-gray-100 dark:border-gray-700">
                <button
                  onClick={() => {
                    setEditing(null);
                    setIsNew(false);
                  }}
                  className="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700"
                >
                  {t('common.cancel', 'Cancel')}
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Save className="w-4 h-4" />
                  {saving ? t('common.saving', 'Saving…') : t('common.save', 'Save')}
                </button>
              </div>
            </div>
          </div>
        );
      })()}
    </PageContainer>
  );
};

export default CatalogWorkspace;
