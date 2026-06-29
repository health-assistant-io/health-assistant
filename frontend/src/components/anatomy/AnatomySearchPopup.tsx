import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Search, ChevronDown, Activity, Heart, Brain, Layers, Box, Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { anatomyService } from '../../services/anatomyService';
import type { AnatomyStructure, AnatomyCategory } from '../../types/anatomy';

interface Props {
  selectedId?: string;
  onSelect: (structure: AnatomyStructure) => void;
  placeholder?: string;
  className?: string;
  innerClassName?: string;
  categoryFilter?: AnatomyCategory[];
}

const CATEGORY_ICONS: Record<AnatomyCategory, React.ReactNode> = {
  SYSTEM: <Layers className="w-4 h-4 text-blue-500" />,
  REGION: <Box className="w-4 h-4 text-green-500" />,
  ORGAN: <Heart className="w-4 h-4 text-red-500" />,
  ORGAN_PART: <Heart className="w-4 h-4 text-orange-500" />,
  TISSUE: <Zap className="w-4 h-4 text-purple-500" />,
  CELL: <Activity className="w-4 h-4 text-pink-500" />,
  SUBSTANCE: <Activity className="w-4 h-4 text-teal-500" />,
  JOINT: <Activity className="w-4 h-4 text-yellow-500" />,
  OTHER: <Activity className="w-4 h-4 text-gray-500" />,
};

const DEBOUNCE_MS = 250;

export const AnatomySearchPopup: React.FC<Props> = ({
  selectedId,
  onSelect,
  placeholder,
  className = '',
  innerClassName = 'px-4 py-3',
  categoryFilter,
}) => {
  const { t } = useTranslation();
  const [structures, setStructures] = useState<AnatomyStructure[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [debouncedTerm, setDebouncedTerm] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const fetchedOnceRef = useRef(false);

  // Debounce the search term
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedTerm(searchTerm), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [searchTerm]);

  const performSearch = useCallback(
    async (term: string) => {
      setIsLoading(true);
      try {
        const activeCategory = categoryFilter && categoryFilter.length === 1 ? categoryFilter[0] : undefined;
        const data = await anatomyService.list({
          search: term || undefined,
          category: activeCategory,
          limit: 100,
        });
        setStructures(data.items);
      } catch (err) {
        console.error('Failed to fetch anatomy structures', err);
        setStructures([]);
      } finally {
        setIsLoading(false);
      }
    },
    [categoryFilter]
  );

  // Initial load (lightweight, no search) so the popup has content on first open
  useEffect(() => {
    if (fetchedOnceRef.current) return;
    fetchedOnceRef.current = true;
    performSearch('');
  }, [performSearch]);

  // Re-query when the debounced term changes
  useEffect(() => {
    if (!isOpen) return;
    performSearch(debouncedTerm);
  }, [debouncedTerm, isOpen, performSearch]);

  // Also refresh whenever the category filter changes
  useEffect(() => {
    if (!isOpen) return;
    performSearch(debouncedTerm);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryFilter]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const filtered = structures.filter((s) => {
    if (categoryFilter && categoryFilter.length > 0 && !categoryFilter.includes(s.category)) {
      return false;
    }
    return true;
  });

  const selected = structures.find((s) => s.id === selectedId);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((prev) => Math.min(prev + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      const item = filtered[activeIndex];
      if (item) {
        onSelect(item);
        setIsOpen(false);
        setSearchTerm('');
        setActiveIndex(-1);
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false);
      setActiveIndex(-1);
    }
  };

  const effectivePlaceholder = placeholder ?? t('anatomy.search_placeholder');

  return (
    <div className={`relative ${className}`} ref={dropdownRef}>
      <div
        className={`w-full ${innerClassName} bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 cursor-pointer flex items-center justify-between transition-all hover:bg-white dark:hover:bg-dark-surface`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center space-x-2">
          {selected ? (
            <>
              {CATEGORY_ICONS[selected.category]}
              <span className="font-bold">{selected.name}</span>
              <span className="text-[9px] bg-gray-100 dark:bg-dark-bg px-1.5 py-0.5 rounded text-gray-400 font-medium">
                {t(`anatomy.categories.${selected.category}`)}
              </span>
            </>
          ) : (
            <>
              <Brain className="w-4 h-4 text-gray-400" />
              <span className="text-gray-400">{effectivePlaceholder}</span>
            </>
          )}
        </div>
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </div>

      {isOpen && (
        <div className="absolute z-[210] w-full mt-2 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="p-3 border-b border-gray-50 dark:border-dark-border sticky top-0 bg-white dark:bg-dark-surface">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                autoFocus
                placeholder={t('anatomy.search_placeholder')}
                className="w-full pl-10 pr-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                value={searchTerm}
                onChange={(e) => {
                  setSearchTerm(e.target.value);
                  setActiveIndex(-1);
                }}
                onKeyDown={handleKeyDown}
              />
            </div>
          </div>

          <div className="max-h-72 overflow-y-auto custom-scrollbar">
            {isLoading ? (
              <div className="p-8 flex justify-center">
                <Activity className="w-6 h-6 text-blue-500 animate-spin" />
              </div>
            ) : filtered.length > 0 ? (
              filtered.map((s, idx) => (
                <div
                  key={s.id}
                  className={`px-4 py-3 text-sm flex items-center justify-between cursor-pointer transition-colors ${
                    activeIndex === idx
                      ? 'bg-blue-50 dark:bg-blue-900/20'
                      : 'hover:bg-blue-50 dark:hover:bg-blue-900/20'
                  } ${selectedId === s.id ? 'bg-blue-50 dark:bg-blue-900/10 text-blue-600 dark:text-blue-400 font-bold' : 'text-gray-700 dark:text-dark-text'}`}
                  onClick={() => {
                    onSelect(s);
                    setIsOpen(false);
                    setSearchTerm('');
                    setActiveIndex(-1);
                  }}
                >
                  <div className="flex items-center space-x-2">
                    {CATEGORY_ICONS[s.category]}
                    <span>{s.name}</span>
                    {s.standard_code && (
                      <span className="text-[9px] bg-gray-100 dark:bg-dark-bg px-1.5 py-0.5 rounded text-gray-400 font-medium uppercase">
                        {s.standard_system}: {s.standard_code}
                      </span>
                    )}
                  </div>
                  <span className="text-[9px] text-gray-400 uppercase tracking-tighter">
                    {t(`anatomy.categories.${s.category}`)}
                  </span>
                </div>
              ))
            ) : (
              <div className="px-4 py-8 text-sm text-gray-400 italic text-center">
                <Brain className="w-8 h-8 mx-auto mb-2 opacity-20" />
                <p>{t('anatomy.no_selection_title')}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
