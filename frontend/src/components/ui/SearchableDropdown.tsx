import React, { useState, useRef, useMemo, useId, useCallback } from 'react';
import { Search, ChevronDown, Check } from 'lucide-react';
import { Popover } from './Popover';

export interface DropdownOption {
  value: string;
  label: string;
  icon?: string; // e.g. for flags
  description?: string;
}

interface Props {
  options: DropdownOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  className?: string;
  disabled?: boolean;
  label?: string;
  error?: string;
}

export const SearchableDropdown: React.FC<Props> = ({
  options,
  value,
  onChange,
  placeholder = "Select an option...",
  searchPlaceholder = "Search...",
  className = "",
  disabled = false,
  label,
  error
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const triggerRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const filteredOptions = useMemo(() => {
    if (!searchTerm.trim()) return options;
    const term = searchTerm.toLowerCase();
    return options.filter(opt =>
      opt.label.toLowerCase().includes(term) ||
      opt.value.toLowerCase().includes(term) ||
      opt.description?.toLowerCase().includes(term)
    );
  }, [options, searchTerm]);

  const selectedOption = useMemo(() =>
    options.find(opt => opt.value === value),
  [options, value]);

  const handleSelect = (val: string) => {
    onChange(val);
    setIsOpen(false);
    setSearchTerm('');
    setActiveIndex(-1);
  };

  const handleTriggerKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (disabled) return;
    switch (e.key) {
      case 'Enter':
      case ' ':
      case 'ArrowDown':
        e.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
          setActiveIndex(0);
        } else {
          setActiveIndex(prev => Math.min(prev + 1, filteredOptions.length - 1));
        }
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (isOpen) {
          setActiveIndex(prev => Math.max(prev - 1, 0));
        }
        break;
      case 'Escape':
        if (isOpen) {
          e.preventDefault();
          setIsOpen(false);
          setActiveIndex(-1);
        }
        break;
      case 'Tab':
        if (isOpen) {
          setIsOpen(false);
          setActiveIndex(-1);
        }
        break;
    }
  }, [disabled, isOpen, filteredOptions.length]);

  const handleListKeyDown = useCallback((e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActiveIndex(prev => Math.min(prev + 1, filteredOptions.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActiveIndex(prev => Math.max(prev - 1, 0));
        break;
      case 'Enter':
        e.preventDefault();
        if (activeIndex >= 0 && activeIndex < filteredOptions.length) {
          handleSelect(filteredOptions[activeIndex].value);
        }
        break;
      case 'Escape':
        e.preventDefault();
        setIsOpen(false);
        setActiveIndex(-1);
        triggerRef.current?.focus();
        break;
    }
  }, [activeIndex, filteredOptions]);

  // Reset active index when search changes
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchTerm(e.target.value);
    setActiveIndex(filteredOptions.length > 0 ? 0 : -1);
  };

  return (
    <div className={`relative ${className}`}>
      {label && (
        <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 mb-1 block">
          {label}
        </label>
      )}

      <div
        ref={triggerRef}
        role="combobox"
        tabIndex={disabled ? -1 : 0}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        aria-controls={isOpen ? listId : undefined}
        aria-activedescendant={isOpen && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        aria-disabled={disabled}
        className={`w-full min-h-[42px] px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm shadow-sm flex items-center justify-between cursor-pointer transition-all focus:ring-2 focus:ring-blue-500/20 focus:outline-none ${disabled ? 'opacity-50 cursor-not-allowed' : 'hover:border-blue-400/50'}`}
        onClick={() => !disabled && setIsOpen(!isOpen)}
        onKeyDown={handleTriggerKeyDown}
      >
        <div className="flex items-center gap-2 truncate">
          {selectedOption ? (
            <>
              {selectedOption.icon && <span className="text-base">{selectedOption.icon}</span>}
              <span className="text-gray-900 dark:text-dark-text font-medium truncate">{selectedOption.label}</span>
            </>
          ) : (
            <span className="text-gray-400">{placeholder}</span>
          )}
        </div>
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform flex-shrink-0 ${isOpen ? 'rotate-180' : ''}`} />
      </div>

      <Popover
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        triggerRef={triggerRef}
        side="bottom"
        align="start"
        sideOffset={4}
      >
        <div
          className="w-full bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200"
          style={{ minWidth: 220 }}
          onKeyDown={handleListKeyDown}
        >
          <div className="p-2 border-b border-gray-50 dark:border-dark-border sticky top-0 bg-white dark:bg-dark-surface">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                type="text"
                autoFocus
                placeholder={searchPlaceholder}
                className="w-full pl-9 pr-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-sm outline-none focus:ring-1 focus:ring-blue-500 dark:text-dark-text"
                value={searchTerm}
                onChange={handleSearchChange}
                onClick={(e) => e.stopPropagation()}
              />
            </div>
          </div>

          <div className="max-h-60 overflow-y-auto custom-scrollbar p-1" role="listbox" id={listId}>
            {filteredOptions.length > 0 ? (
              filteredOptions.map((opt, idx) => {
                const isSelected = opt.value === value;
                const isActive = idx === activeIndex;
                return (
                  <div
                    key={opt.value}
                    id={`${listId}-${idx}`}
                    role="option"
                    aria-selected={isSelected}
                    className={`px-4 py-2.5 text-sm flex items-center justify-between cursor-pointer transition-colors rounded-lg m-0.5 ${
                      isActive ? 'bg-blue-50 dark:bg-blue-900/20' : ''
                    } ${isSelected ? 'bg-blue-50/50 dark:bg-blue-900/10' : ''} hover:bg-blue-50 dark:hover:bg-blue-900/20`}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleSelect(opt.value);
                    }}
                    onMouseEnter={() => setActiveIndex(idx)}
                  >
                    <div className="flex items-center gap-3 truncate">
                      {opt.icon && <span className="text-base flex-shrink-0">{opt.icon}</span>}
                      <div className="flex flex-col truncate">
                        <span className={`font-bold truncate ${isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-gray-700 dark:text-dark-text'}`}>
                          {opt.label}
                        </span>
                        {opt.description && <span className="text-[10px] text-gray-400 uppercase tracking-tighter truncate">{opt.description}</span>}
                      </div>
                    </div>
                    {isSelected && <Check className="w-4 h-4 text-blue-600" />}
                  </div>
                );
              })
            ) : (
              <div className="px-4 py-8 text-sm text-gray-400 italic text-center">
                <Search className="w-8 h-8 mx-auto mb-2 opacity-20" />
                <p>No matches found</p>
              </div>
            )}
          </div>
        </div>
      </Popover>

      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
};
