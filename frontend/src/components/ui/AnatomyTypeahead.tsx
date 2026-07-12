import { useState, useEffect, useRef, useCallback } from 'react';
import { Search, Loader2, X, Check } from 'lucide-react';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure } from '../../types/anatomy';
import { Popover } from './Popover';

export interface AnatomyTypeaheadSelection {
  id: string;
  name: string;
  slug: string;
}

interface AnatomyTypeaheadProps {
  value?: string | null;
  initial?: AnatomyStructure | null;
  onSelect: (selection: AnatomyTypeaheadSelection | null) => void;
  placeholder?: string;
  className?: string;
  clearable?: boolean;
}

/**
 * Server-backed searchable picker for ``anatomy_structures``.
 *
 * Mirrors ``TaxonomyTypeahead`` but resolves anatomy nodes — used by the
 * TaxonomyManager relationship panel to create ``concept -> anatomy`` edges
 * (e.g. Echocardiography IMAGES heart) without duplicating organs into the
 * concept table.
 */
export default function AnatomyTypeahead({
  value,
  initial,
  onSelect,
  placeholder = 'Search anatomy…',
  className = '',
  clearable = true,
}: AnatomyTypeaheadProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [term, setTerm] = useState('');
  const [results, setResults] = useState<AnatomyStructure[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<AnatomyStructure | null>(initial ?? null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const doSearch = useCallback(async (t: string) => {
    if (!t.trim()) { setResults([]); return; }
    setLoading(true);
    try {
      const res = await anatomyService.list({ search: t, limit: 20 });
      setResults(res.items);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isOpen || !term) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(term), 300);
    return () => clearTimeout(debounceRef.current);
  }, [term, isOpen, doSearch]);

  const handleSelect = (s: AnatomyStructure) => {
    setSelected(s);
    onSelect({ id: s.id, name: s.name, slug: s.slug });
    setIsOpen(false);
    setTerm('');
  };

  const handleClear = () => {
    setSelected(null);
    onSelect(null);
  };

  if (selected) {
    return (
      <div className={`flex items-center gap-2 ${className}`} ref={triggerRef}>
        <div className="flex items-center gap-2 rounded-lg border border-emerald-200 dark:border-emerald-900 bg-emerald-50 dark:bg-emerald-950/30 px-3 py-1.5 flex-1">
          <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
          <span className="text-sm font-medium truncate">{selected.name}</span>
          <span className="text-[10px] text-slate-400 uppercase">{selected.slug}</span>
          {clearable && (
            <button onClick={handleClear} className="ml-auto text-slate-400 hover:text-slate-600 dark:hover:text-slate-200">
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`relative ${className}`} ref={triggerRef}>
      <div
        className="flex items-center gap-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 cursor-text hover:border-emerald-400 dark:hover:border-emerald-500 transition-colors"
        onClick={() => setIsOpen(true)}
      >
        <Search className="w-4 h-4 text-slate-400" />
        {isOpen ? (
          <input
            autoFocus
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder={placeholder}
            className="flex-1 bg-transparent outline-none text-sm text-slate-900 dark:text-slate-100 placeholder-slate-400"
            onKeyDown={(e) => { if (e.key === 'Escape') { setIsOpen(false); setTerm(''); } }}
          />
        ) : (
          <span className="flex-1 text-sm text-slate-400">{placeholder}</span>
        )}
        {loading && <Loader2 className="w-4 h-4 animate-spin text-emerald-500" />}
      </div>

      <Popover
        isOpen={isOpen}
        onClose={() => { setIsOpen(false); setTerm(''); }}
        triggerRef={triggerRef}
        side="bottom"
        align="start"
        sideOffset={4}
      >
        <div className="w-full max-h-64 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-lg" style={{ minWidth: 260 }}>
          {results.length === 0 && !loading && (
            <div className="px-3 py-4 text-center text-sm text-slate-400">
              {term ? 'No anatomy matches' : 'Start typing to search anatomy…'}
            </div>
          )}
          {results.map((s) => (
            <button
              key={s.id}
              onClick={() => handleSelect(s)}
              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors text-left"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium truncate">{s.name}</div>
                <div className="text-xs text-slate-400 truncate">
                  {s.slug}{s.description ? ` · ${s.description}` : ''}
                </div>
              </div>
              {value === s.id && <Check className="w-4 h-4 text-emerald-500 shrink-0" />}
            </button>
          ))}
        </div>
      </Popover>
    </div>
  );
}
