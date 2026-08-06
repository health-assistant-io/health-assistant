/**
 * Catalog toolbar — the single consolidated control band above the split
 * layout. Groups every filter into one place so the list + preview panes get
 * the full remaining width:
 *   [Type ▾ searchable] [Scope ▾] [≡ cards] [+ New]
 *   [ FilterBar (facet chips — kind/class/category/…, per catalog type) ]
 *
 * Item search is handled by the global page-search (header SearchLauncher),
 * not a local input — that's why there's no search box here. Wraps on narrow
 * widths.
 */
import React, { useState } from 'react';
import { Plus, List as ListIcon, LayoutGrid, Download, SlidersHorizontal, ChevronDown, GitBranch } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CatalogTypeSelect } from './CatalogTypeSelect';
import type { CatalogTypeMeta } from '../../types/catalog';
import { downloadSeedsZip } from '../../services/seedService';

interface CatalogToolbarProps {
  types: CatalogTypeMeta[];
  activeType: string;
  onSelectType: (type: string) => void;

  scopeFilter: string;
  onScopeChange: (scope: string) => void;

  /** Unified exploration mode: list | card (item rendering) or graph (ontology). */
  explorationMode: 'list' | 'card' | 'graph';
  onExplorationModeChange: (mode: 'list' | 'card' | 'graph') => void;

  /** When undefined, the New button is hidden (role-gated for curated types). */
  onNew?: () => void;

  /** When true, shows the SYSTEM_ADMIN-only "Export seeds" button. */
  canExportSeeds?: boolean;

  /**
   * Optional filter bar rendered as a second row below the main controls
   * (the per-type facet chips — kind/class/category/…). When undefined, no
   * second row is shown. On mobile (< sm) this row is collapsed behind a
   * "Filters" toggle button to save vertical space; on desktop it's always
   * visible.
   */
  filterBar?: React.ReactNode;

  /** When true, the mobile Filters button shows an active-filter dot. */
  hasActiveFilters?: boolean;

  /**
   * External click handler for the mobile Filters button. When provided, the
   * button calls this instead of toggling the inline `filterBar` collapse —
   * used in graph mode where the button opens the graph filter sheet rather
   * than the facet chips. The Filters button renders when either `filterBar`
   * or `onFiltersClick` is provided.
   */
  onFiltersClick?: () => void;
}

const SCOPE_OPTIONS: { value: string; labelKey: string; fallback: string }[] = [
  { value: '', labelKey: 'catalogs.scope_all', fallback: 'All' },
  { value: 'system', labelKey: 'catalogs.scope_system', fallback: 'System' },
  { value: 'tenant', labelKey: 'catalogs.scope_tenant', fallback: 'Tenant' },
  { value: 'mine', labelKey: 'catalogs.scope_mine', fallback: 'Mine' },
];

export const CatalogToolbar: React.FC<CatalogToolbarProps> = ({
  types,
  activeType,
  onSelectType,
  scopeFilter,
  onScopeChange,
  explorationMode,
  onExplorationModeChange,
  onNew,
  canExportSeeds,
  filterBar,
  hasActiveFilters,
  onFiltersClick,
}) => {
  const { t } = useTranslation();
  // Mobile-only: the FilterBar is collapsed by default to save vertical
  // space; user toggles it open. Desktop always shows it.
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 sm:px-3 py-1.5 sm:py-2">
      <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
      <CatalogTypeSelect types={types} activeType={activeType} onSelect={onSelectType} />

      {/* Scope segmented control */}
      <div className="flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
        {SCOPE_OPTIONS.map((opt) => (
          <button
            key={opt.value || 'all'}
            onClick={() => onScopeChange(opt.value)}
            className={`px-2 sm:px-2.5 py-1.5 text-xs font-medium transition-colors ${
              scopeFilter === opt.value
                ? 'bg-blue-600 text-white'
                : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
            title={t(opt.labelKey, opt.fallback)}
          >
            {t(opt.labelKey, opt.fallback)}
          </button>
        ))}
      </div>

      {/* Filters toggle. In graph mode (`onFiltersClick`) it shows on all
          viewports (opens the graph filter side panel / bottom sheet); in
          list/card modes it's mobile-only (collapses the facet FilterBar). */}
      {(filterBar || onFiltersClick) && (
        <button
          type="button"
          onClick={() => (onFiltersClick ? onFiltersClick() : setMobileFiltersOpen((v) => !v))}
          className={`${onFiltersClick ? '' : 'sm:hidden'} flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-bold rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-800 shrink-0`}
          aria-expanded={onFiltersClick ? undefined : mobileFiltersOpen}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span>{t('catalogs.filters', { defaultValue: 'Filters' })}</span>
          {hasActiveFilters && (
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500" aria-label="active" />
          )}
          {!onFiltersClick && (
            <ChevronDown className={`w-3.5 h-3.5 transition-transform ${mobileFiltersOpen ? 'rotate-180' : ''}`} />
          )}
        </button>
      )}

      {/* Spacer pushes list controls right on wide screens */}
      <div className="flex-1 min-w-2" />

      {/* Exploration mode: List | Cards | Graph (icon-only on mobile, labels on sm+) */}
      <div className="flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden shrink-0">
        <button
          onClick={() => onExplorationModeChange('list')}
          title={t('catalogs.view_list', 'List view')}
          className={`flex items-center gap-1.5 px-2 sm:px-2.5 py-1.5 text-xs font-medium ${explorationMode === 'list' ? 'bg-blue-600 text-white' : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'}`}
        >
          <ListIcon className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">{t('catalogs.view_list', 'List')}</span>
        </button>
        <button
          onClick={() => onExplorationModeChange('card')}
          title={t('catalogs.view_cards', 'Card view')}
          className={`flex items-center gap-1.5 px-2 sm:px-2.5 py-1.5 text-xs font-medium ${explorationMode === 'card' ? 'bg-blue-600 text-white' : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'}`}
        >
          <LayoutGrid className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">{t('catalogs.view_cards', 'Cards')}</span>
        </button>
        <button
          onClick={() => onExplorationModeChange('graph')}
          title={t('catalogs.view_graph', 'Graph view')}
          className={`flex items-center gap-1.5 px-2 sm:px-2.5 py-1.5 text-xs font-medium ${explorationMode === 'graph' ? 'bg-blue-600 text-white' : 'text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'}`}
        >
          <GitBranch className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">{t('catalogs.view_graph', 'Graph')}</span>
        </button>
      </div>

      {canExportSeeds && (
        <button
          onClick={() => downloadSeedsZip()}
          title={t('catalogs.export_seeds_hint', 'Download the full taxonomy + catalogs as a ZIP of seed JSON')}
          className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 shrink-0"
        >
          <Download className="w-4 h-4" />
          <span className="hidden sm:inline">{t('catalogs.export_seeds', 'Export seeds')}</span>
        </button>
      )}

      {onNew && (
        <button
          onClick={onNew}
          className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 shrink-0"
        >
          <Plus className="w-4 h-4" /> {t('common.new', 'New')}
        </button>
      )}
      </div>

      {filterBar && (
        <div className={`${mobileFiltersOpen ? 'flex' : 'hidden'} sm:flex flex-wrap items-center gap-1.5 sm:gap-2 pt-1.5 sm:pt-2 mt-1.5 sm:mt-2 border-t border-gray-100 dark:border-gray-700`}>
          {filterBar}
        </div>
      )}
    </div>
  );
};
