import { useState, useEffect, useRef, useCallback } from 'react';
import { Search, Loader2, X, Check } from 'lucide-react';
import { DynamicIcon } from './DynamicIcon';
import type { IconConfig } from './DynamicIcon';
import { searchConcepts } from '../../services/conceptService';
import type { Concept, ConceptKind } from '../../types/concept';

interface TaxonomyTypeaheadProps {
  kind?: ConceptKind;
  /** Currently-selected concept id (drives the check mark in results). */
  value?: string | null;
  /** Pre-populate the selected display (used by edit forms to show an
   *  existing link without requiring the user to re-search). */
  initialConcept?: Concept | null;
  onSelect: (concept: Concept | null) => void;
  placeholder?: string;
  className?: string;
  clearable?: boolean;
}

export default function TaxonomyTypeahead({
  kind,
  value,
  initialConcept,
  onSelect,
  placeholder = 'Search...',
  className = '',
  clearable = true,
}: TaxonomyTypeaheadProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [results, setResults] = useState<Concept[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Concept | null>(initialConcept ?? null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const doSearch = useCallback(
    async (term: string) => {
      if (term.trim().length < 1) {
        setResults([]);
        return;
      }
      setLoading(true);
      try {
        const data = await searchConcepts(term, kind, 20);
        setResults(data);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [kind],
  );

  useEffect(() => {
    if (!isOpen || !searchTerm) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(searchTerm), 300);
    return () => clearTimeout(debounceRef.current);
  }, [searchTerm, isOpen, doSearch]);

  const handleSelect = (concept: Concept) => {
    setSelected(concept);
    onSelect(concept);
    setIsOpen(false);
    setSearchTerm('');
  };

  const handleClear = () => {
    setSelected(null);
    onSelect(null);
  };

  if (selected) {
    return (
      <div className={`flex items-center gap-2 ${className}`} ref={dropdownRef}>
        <div
          className="flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 flex-1"
          style={
            selected.color
              ? { borderColor: `${selected.color}40`, backgroundColor: `${selected.color}10` }
              : undefined
          }
        >
          {selected.icon && (
            <DynamicIcon
              icon={selected.icon as IconConfig}
              className="w-4 h-4"
              color={selected.color || undefined}
            />
          )}
          <span className="text-sm font-medium truncate">{selected.name}</span>
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
    <div className={`relative ${className}`} ref={dropdownRef}>
      <div
        className="flex items-center gap-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 cursor-text hover:border-blue-400 dark:hover:border-blue-500 transition-colors"
        onClick={() => setIsOpen(true)}
      >
        <Search className="w-4 h-4 text-slate-400" />
        {isOpen ? (
          <input
            autoFocus
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder={placeholder}
            className="flex-1 bg-transparent outline-none text-sm text-slate-900 dark:text-slate-100 placeholder-slate-400"
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setIsOpen(false);
                setSearchTerm('');
              }
            }}
          />
        ) : (
          <span className="flex-1 text-sm text-slate-400">{placeholder}</span>
        )}
        {loading && <Loader2 className="w-4 h-4 animate-spin text-blue-500" />}
      </div>

      {isOpen && (
        <div className="absolute z-50 mt-1 w-full max-h-64 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-lg">
          {results.length === 0 && !loading && (
            <div className="px-3 py-4 text-center text-sm text-slate-400">
              {searchTerm ? 'No matches found' : 'Start typing to search...'}
            </div>
          )}
          {results.map((c) => (
            <button
              key={c.id}
              onClick={() => handleSelect(c)}
              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors text-left"
            >
              {c.icon && (
                <DynamicIcon
                  icon={c.icon as IconConfig}
                  className="w-4 h-4 shrink-0"
                  color={c.color || undefined}
                />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium truncate">{c.name}</div>
                {c.description && (
                  <div className="text-xs text-slate-400 truncate">{c.description}</div>
                )}
              </div>
              {value === c.id && <Check className="w-4 h-4 text-blue-500 shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
