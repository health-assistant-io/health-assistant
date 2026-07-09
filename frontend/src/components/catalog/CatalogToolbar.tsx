/**
 * Catalog toolbar — the single consolidated control band above the split
 * layout. Groups every filter into one place so the list + preview panes get
 * the full remaining width:
 *   [Type ▾ searchable] [Scope ▾] [Class chips] [≡ cards] [+ New]
 *
 * Item search is handled by the global page-search (header SearchLauncher),
 * not a local input — that's why there's no search box here. Wraps on narrow
 * widths.
 */
import React from 'react';
import { Plus, List as ListIcon, LayoutGrid, Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CatalogTypeSelect } from './CatalogTypeSelect';
import { CLASS_COLOR } from '../../types/anatomy';
import type { CatalogTypeMeta } from '../../types/catalog';
import { downloadSeedsZip } from '../../services/seedService';

interface CatalogToolbarProps {
  types: CatalogTypeMeta[];
  activeType: string;
  onSelectType: (type: string) => void;

  scopeFilter: string;
  onScopeChange: (scope: string) => void;

  /** Anatomy-only: class concept chips. Also reused for concept-kind chips. */
  anatomyClasses: { slug: string; name: string }[];
  classFilter: string;
  onClassChange: (cls: string) => void;

  viewMode: 'list' | 'card';
  onViewModeChange: (mode: 'list' | 'card') => void;

  /** When undefined, the New button is hidden (role-gated for curated types). */
  onNew?: () => void;

  /** When true, shows the SYSTEM_ADMIN-only "Export seeds" button. */
  canExportSeeds?: boolean;
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
  anatomyClasses,
  classFilter,
  onClassChange,
  viewMode,
  onViewModeChange,
  onNew,
  canExportSeeds,
}) => {
  const { t } = useTranslation();

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2">
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

      {/* Anatomy-only class chips */}
      {anatomyClasses.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          {anatomyClasses.map((cls) => {
            const activeChip = classFilter
              ? classFilter.split(',').includes(cls.slug)
              : false;
            const color = CLASS_COLOR(cls.slug);
            return (
              <button
                key={cls.slug}
                onClick={() => {
                  const set = new Set(classFilter ? classFilter.split(',') : []);
                  if (set.has(cls.slug)) set.delete(cls.slug);
                  else set.add(cls.slug);
                  onClassChange([...set].join(','));
                }}
                className={`px-2 py-0.5 text-[11px] font-bold rounded-full border transition-all ${
                  activeChip
                    ? 'text-white border-transparent'
                    : 'border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                }`}
                style={activeChip ? { backgroundColor: color } : undefined}
              >
                {cls.name}
              </button>
            );
          })}
          {classFilter && (
            <button
              onClick={() => onClassChange('')}
              className="text-[11px] text-gray-400 hover:text-red-500 ml-1"
            >
              {t('common.clear', 'Clear')}
            </button>
          )}
        </div>
      )}

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
  );
};
