/**
 * Catalog item switcher — a combobox for the Relations tab that shows the
 * currently-selected item and lets the user search + swap it without bouncing
 * back to the Browser tab (Phase D follow-up / user request #5).
 *
 * Renders as a button with the current label + chevron; clicking opens a
 * popover with a search box scoped to the active catalog type
 * (`searchCatalogs` with a `types` filter). Selecting a hit calls `onSelect`.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Search, ChevronDown, Check } from 'lucide-react';
import { searchCatalogs } from '../../services/catalogService';
import type { CatalogSearchHit } from '../../types/catalog';

interface CatalogItemSwitcherProps {
  catalogType: string;
  currentItemId?: string;
  currentLabel?: string;
  onSelect: (id: string, label: string) => void;
  placeholder?: string;
}

export const CatalogItemSwitcher: React.FC<CatalogItemSwitcherProps> = ({
  catalogType,
  currentItemId,
  currentLabel,
  onSelect,
  placeholder = 'Select an item…',
}) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<CatalogSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Debounced search scoped to the active catalog type.
  useEffect(() => {
    if (!open || query.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const resp = await searchCatalogs(query, { types: catalogType, limit: 12 });
        if (!cancelled) setResults(resp.results);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [open, query, catalogType]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const pick = (hit: CatalogSearchHit) => {
    onSelect(hit.id, hit.label);
    setOpen(false);
    setQuery('');
    setResults([]);
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 max-w-[16rem] rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-1.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
        title={currentLabel ?? placeholder}
      >
        <span className="truncate">
          {currentLabel ?? placeholder}
        </span>
        <ChevronDown className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute z-30 mt-1 w-72 max-w-[80vw] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg">
          <div className="relative p-2 border-b border-gray-100 dark:border-gray-700">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search to switch item…"
              className="w-full pl-7 pr-3 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 focus:ring-2 focus:ring-blue-500 outline-none"
            />
          </div>
          <div className="max-h-64 overflow-auto">
            {searching && results.length === 0 ? (
              <p className="px-3 py-2 text-xs text-gray-400">Searching…</p>
            ) : results.length === 0 ? (
              <p className="px-3 py-2 text-xs text-gray-400">
                {query.length < 2 ? 'Type at least 2 characters…' : 'No matches.'}
              </p>
            ) : (
              results.map((r) => {
                const active = r.id === currentItemId;
                return (
                  <button
                    key={r.id}
                    onClick={() => pick(r)}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-blue-50 dark:hover:bg-blue-900/20 ${
                      active ? 'text-blue-600 dark:text-blue-400' : ''
                    }`}
                  >
                    <span className="flex-1 truncate">{r.label}</span>
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
