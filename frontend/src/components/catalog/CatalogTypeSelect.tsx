/**
 * Catalog type select — a searchable dropdown that replaces the old left-rail
 * list of catalog types. Renders each type's `DynamicIcon` + name, keeps the
 * taxonomy-link dot, and supports type-ahead filtering so a tenant with many
 * catalogs can jump quickly. Replaces ~224px of fixed left-rail width with a
 * compact control in the consolidated toolbar.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Check, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DynamicIcon } from '../ui/DynamicIcon';
import type { CatalogTypeMeta } from '../../types/catalog';

interface CatalogTypeSelectProps {
  types: CatalogTypeMeta[];
  activeType: string;
  onSelect: (type: string) => void;
  /** When true, prepends an "All" entry (value = `allValue`) above the list,
   *  for pickers that want a cross-catalog option. Default off (unchanged
   *  behavior for the workspace toolbar). */
  allowAll?: boolean;
  /** The value emitted when "All" is selected (default `'all'`). */
  allValue?: string;
  /** Label shown for the "All" entry (already i18n'd by the caller). */
  allLabel?: string;
  /** When true, the "All" entry renders disabled (greyed, non-clickable).
   *  Used when the picker is scoped to a single distinctive catalog (e.g. a
   *  body-location field locked to anatomy) — the option stays visible so the
   *  restriction is communicated, but can't be selected. */
  allDisabled?: boolean;
}

export const CatalogTypeSelect: React.FC<CatalogTypeSelectProps> = ({
  types,
  activeType,
  onSelect,
  allowAll = false,
  allValue = 'all',
  allLabel = 'All',
  allDisabled = false,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const filtered = useMemo(() => {
    if (!q.trim()) return types;
    const term = q.toLowerCase();
    return types.filter((tg) => tg.type.toLowerCase().includes(term));
  }, [types, q]);

  const active = allowAll && activeType === allValue
    ? null
    : types.find((tg) => tg.type === activeType) ?? null;
  const label = active
    ? active.type
    : allowAll
      ? allLabel
      : t('catalogs.type_select_placeholder', 'Select catalog');

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 min-w-[10rem] rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
        title={t('catalogs.type_select_title', 'Catalog type')}
      >
        {active && <DynamicIcon icon={active.ui.icon} className="w-4 h-4 shrink-0" />}
        <span className="flex-1 truncate text-left font-medium capitalize">{label}</span>
        <ChevronDown
          className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute z-dropdown mt-1 w-60 max-w-[80vw] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg">
          <div className="relative p-2 border-b border-gray-100 dark:border-gray-700">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t('catalogs.type_select_search', 'Filter catalogs…')}
              className="w-full pl-7 pr-3 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none"
            />
          </div>
          <div className="max-h-72 overflow-auto">
            {filtered.length === 0 && !(allowAll && !q.trim()) ? (
              <p className="px-3 py-2 text-xs text-gray-400">
                {t('catalogs.no_items', 'No items found.')}
              </p>
            ) : (
              <>
                {allowAll && !q.trim() && (
                  <button
                    disabled={allDisabled}
                    onClick={() => {
                      if (allDisabled) return;
                      onSelect(allValue);
                      setOpen(false);
                      setQ('');
                    }}
                    title={
                      allDisabled
                        ? t(
                            'catalogs.type_all_disabled',
                            'Locked to a single catalog for this field',
                          )
                        : undefined
                    }
                    className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${
                      allDisabled
                        ? 'cursor-not-allowed opacity-40'
                        : 'hover:bg-blue-50 dark:hover:bg-blue-900/20'
                    } ${
                      !allDisabled && activeType === allValue
                        ? 'text-blue-600 dark:text-blue-400 font-medium'
                        : ''
                    }`}
                  >
                    <span className="w-4 h-4 shrink-0" />
                    <span className="flex-1 truncate">{allLabel}</span>
                    {!allDisabled && activeType === allValue && (
                      <Check className="w-3.5 h-3.5 shrink-0" />
                    )}
                  </button>
                )}
                {filtered.map((tg) => {
                  const isActive = tg.type === activeType;
                  return (
                    <button
                      key={tg.type}
                      onClick={() => {
                        onSelect(tg.type);
                        setOpen(false);
                        setQ('');
                      }}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-blue-50 dark:hover:bg-blue-900/20 ${
                        isActive ? 'text-blue-600 dark:text-blue-400 font-medium' : ''
                      }`}
                    >
                      <DynamicIcon icon={tg.ui.icon} className="w-4 h-4 shrink-0" />
                      <span className="flex-1 truncate capitalize">{tg.type}</span>
                      {tg.has_concept_link && (
                        <span
                          className="h-1.5 w-1.5 rounded-full bg-blue-400 shrink-0"
                          title={t('catalogs.taxonomy_link', 'Has taxonomy link')}
                        />
                      )}
                      {isActive && <Check className="w-3.5 h-3.5 shrink-0" />}
                    </button>
                  );
                })}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
