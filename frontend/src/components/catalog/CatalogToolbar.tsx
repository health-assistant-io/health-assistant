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
import React from 'react';
import { Plus, List as ListIcon, LayoutGrid, Download } from 'lucide-react';
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

  viewMode: 'list' | 'card';
  onViewModeChange: (mode: 'list' | 'card') => void;

  /** When undefined, the New button is hidden (role-gated for curated types). */
  onNew?: () => void;

  /** When true, shows the SYSTEM_ADMIN-only "Export seeds" button. */
  canExportSeeds?: boolean;

  /**
   * Optional filter bar rendered as a second row below the main controls
   * (the per-type facet chips — kind/class/category/…). When undefined, no
   * second row is shown.
   */
  filterBar?: React.ReactNode;
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
  viewMode,
  onViewModeChange,
  onNew,
  canExportSeeds,
  filterBar,
}) => {
  const { t } = useTranslation();

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
      <CatalogTypeSelect types={types} activeType={activeType} onSelect={onSelectType} />

      {/* Scope segmented control */}
      <div className="flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
        {SCOPE_OPTIONS.map((opt) => (
          <button
            key={opt.value || 'all'}
            onClick={() => onScopeChange(opt.value)}
            className={`px-2.5 py-1.5 text-xs font-medium transition-colors ${
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

      {/* Spacer pushes list controls right on wide screens */}
      <div className="flex-1 min-w-2" />

      {/* View toggle */}
      <div className="flex items-center rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden shrink-0">
        <button
          onClick={() => onViewModeChange('list')}
          title={t('catalogs.view_list', 'List view')}
          className={`p-2 ${viewMode === 'list' ? 'bg-blue-600 text-white' : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700'}`}
        >
          <ListIcon className="w-4 h-4" />
        </button>
        <button
          onClick={() => onViewModeChange('card')}
          title={t('catalogs.view_cards', 'Card view')}
          className={`p-2 ${viewMode === 'card' ? 'bg-blue-600 text-white' : 'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700'}`}
        >
          <LayoutGrid className="w-4 h-4" />
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
        <div className="flex flex-wrap items-center gap-2 pt-2 mt-2 border-t border-gray-100 dark:border-gray-700">
          {filterBar}
        </div>
      )}
    </div>
  );
};
