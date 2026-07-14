import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Check, Search, X } from 'lucide-react';
import { filterBiomarkers } from '../../../utils/searchUtils';

export interface SearchableBiomarkerSelectProps {
  value: string | string[];
  options: any[];
  onChange: (value: any) => void;
  multiple?: boolean;
  placeholder?: string;
  className?: string;
  discreet?: boolean;
}

export const SearchableBiomarkerSelect: React.FC<SearchableBiomarkerSelectProps> = ({ 
  value, 
  options, 
  onChange, 
  multiple = false,
  placeholder, 
  className, 
  discreet 
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getDisplayValue = () => {
    if (multiple) {
      if (!Array.isArray(value) || value.length === 0) return placeholder || t('common.select');
      if (value.length === 1) {
        const option = options.find(opt => (opt.slug === value[0] || opt.name === value[0] || opt === value[0]));
        return typeof option === 'object' ? (option.displayName || option.name) : (option || value[0]);
      }
      return `${value.length} ${t('common.selected') || 'selected'}`;
    } else {
      if (!value) return placeholder || t('common.select');
      const option = options.find(opt => (opt.slug === value || opt.name === value || opt === value));
      return typeof option === 'object' ? (option.displayName || option.name) : (option || value);
    }
  };

  const filteredOptions = useMemo(() => {
    // Ensure all options have a name and slug for the filterBiomarkers function
    const normalizedOptions = options.map(opt => {
      if (typeof opt === 'string') return { name: opt, slug: opt, displayName: opt };
      return { 
        ...opt, 
        name: opt.name || opt.displayName, 
        slug: opt.slug || opt.id || (opt.displayName ? opt.displayName.toLowerCase().replace(/\s+/g, '-') : (opt.name ? opt.name.toLowerCase().replace(/\s+/g, '-') : ''))
      };
    });
    return filterBiomarkers(normalizedOptions, searchTerm);
  }, [options, searchTerm]);

  const toggleSelection = (val: string) => {
    if (multiple) {
      const currentValues = Array.isArray(value) ? value : [];
      if (currentValues.includes(val)) {
        onChange(currentValues.filter(v => v !== val));
      } else {
        onChange([...currentValues, val]);
      }
    } else {
      onChange(val);
      setIsOpen(false);
    }
  };

  const isSelected = (val: string) => {
    if (multiple) {
      return Array.isArray(value) && value.includes(val);
    }
    return value === val;
  };

  const clearSelection = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange(multiple ? [] : '');
  };

  return (
    <div className={`relative ${className || ''} nodrag`} ref={dropdownRef}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setIsOpen(!isOpen); }}
        className={`w-full flex items-center justify-between transition-all outline-none focus:ring-2 focus:ring-blue-500/20 ${discreet 
          ? 'px-2 py-1 bg-transparent border-none text-[10px] font-black uppercase tracking-widest text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-dark-surface rounded-lg' 
          : 'px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-[10px] font-black uppercase tracking-widest text-gray-500 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-surface hover:border-blue-300 shadow-sm'}`}
      >
        <span className="truncate mr-2 flex-1 text-left">{getDisplayValue()}</span>
        <div className="flex items-center space-x-1">
          {((multiple && Array.isArray(value) && value.length > 0) || (!multiple && value)) && (
            <button
              type="button"
              onClick={clearSelection}
              aria-label="Clear selection"
              className="p-1 rounded-full hover:bg-gray-200 dark:hover:bg-dark-border text-gray-400 hover:text-gray-600 dark:hover:text-dark-text transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          )}
          <ChevronDown className={`w-3 h-3 transition-transform duration-300 ${isOpen ? 'rotate-180' : ''}`} />
        </div>
      </button>
      
      {isOpen && (
        <div className="absolute top-full right-0 mt-1.5 z-[150] bg-white/95 dark:bg-dark-surface/95 backdrop-blur-md border border-gray-200 dark:border-dark-border rounded-xl shadow-2xl overflow-hidden w-64 animate-in fade-in slide-in-from-top-1 duration-200">
          <div className="p-2 border-b border-gray-100 dark:border-dark-border">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                type="text"
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 dark:text-dark-text"
                placeholder={t('common.search') || 'Search...'}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                autoFocus
              />
            </div>
          </div>
          
          <div className="py-1.5 max-h-60 overflow-y-auto custom-scrollbar">
            {filteredOptions.length === 0 ? (
              <div className="px-4 py-3 text-[10px] text-gray-400 italic">No options found</div>
            ) : (
              filteredOptions.map((opt) => {
                const label = opt.displayName || opt.name || opt.slug;
                const val = opt.slug || opt.id || opt.name;
                const selected = isSelected(val);

                return (
                  <button
                    key={val}
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleSelection(val);
                    }}
                    className={`w-full text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider transition-all flex items-center justify-between ${selected ? 'bg-blue-600 text-white' : 'text-gray-600 dark:text-dark-text hover:bg-blue-50 dark:hover:bg-blue-900/40'}`}
                  >
                    <span className="truncate pr-2">{label}</span>
                    {selected && <Check className="w-3 h-3 flex-shrink-0" />}
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
