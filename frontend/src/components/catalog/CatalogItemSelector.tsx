/**
 * Catalog item selector — the Layer-1 dropdown that picks the "current item"
 * driving the Info + Relations tabs. Defaults to "All items" (no selection);
 * selecting an item sets `?item=` and is reversible by re-picking "All".
 *
 * Unlike `CatalogItemSwitcher` (which live-searches the backend), this reads
 * the already-loaded page of items — no extra round-trip — and always offers
 * the explicit "All" option.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Check, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { CatalogItem } from '../../types/catalog';

interface CatalogItemSelectorProps {
  items: CatalogItem[];
  selectedItemId?: string;
  /** Called with the item id, or '' for "All". */
  onSelect: (id: string) => void;
}

export const CatalogItemSelector: React.FC<CatalogItemSelectorProps> = ({
  items,
  selectedItemId,
  onSelect,
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
    if (!q.trim()) return items;
    const term = q.toLowerCase();
    return items.filter((it) =>
      String(it.name ?? it.slug ?? it.id).toLowerCase().includes(term),
    );
  }, [items, q]);

  const selected = items.find((it) => String(it.id) === selectedItemId);
  const label = selected
    ? String(selected.name ?? selected.slug ?? selected.id)
    : t('catalogs.selector_all', 'All items');

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 max-w-[18rem] rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
        title={label}
      >
        <span className="truncate font-medium">{label}</span>
        <ChevronDown
          className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute z-dropdown mt-1 w-72 max-w-[80vw] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg">
          <div className="relative p-2 border-b border-gray-100 dark:border-gray-700">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t('catalogs.selector_search', 'Filter items…')}
              className="w-full pl-7 pr-3 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none"
            />
          </div>
          <div className="max-h-72 overflow-auto">
            <button
              onClick={() => {
                onSelect('');
                setOpen(false);
                setQ('');
              }}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700 ${
                !selectedItemId ? 'text-blue-600 dark:text-blue-400 font-medium' : ''
              }`}
            >
              <span className="flex-1 truncate">
                {t('catalogs.selector_all', 'All items')}
              </span>
              {!selectedItemId && <Check className="w-3.5 h-3.5 shrink-0" />}
            </button>
            {filtered.length === 0 ? (
              <p className="px-3 py-2 text-xs text-gray-400">
                {t('catalogs.no_items', 'No items found.')}
              </p>
            ) : (
              filtered.map((it) => {
                const id = String(it.id);
                const active = id === selectedItemId;
                return (
                  <button
                    key={id}
                    onClick={() => {
                      onSelect(id);
                      setOpen(false);
                      setQ('');
                    }}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-blue-50 dark:hover:bg-blue-900/20 ${
                      active ? 'text-blue-600 dark:text-blue-400 font-medium' : ''
                    }`}
                  >
                    <span className="flex-1 truncate">
                      {String(it.name ?? it.slug ?? it.id)}
                    </span>
                    {active && <Check className="w-3.5 h-3.5 shrink-0" />}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
};
