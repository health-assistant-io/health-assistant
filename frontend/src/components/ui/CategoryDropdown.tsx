import React, { useState, useRef, ReactNode } from 'react';
import { Grid, ChevronDown, CheckCircle2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DynamicIcon } from './DynamicIcon';
import { Popover } from './Popover';

interface CategoryTab {
  name: string;
  count: number;
  icon: any; // Allow IconConfig or string
  color: string | null;
  id?: string;
}

interface Props {
  tabs: CategoryTab[];
  selectedCategories: string[];
  onToggleCategory: (category: string) => void;
  label?: string;
  isMultiSelect?: boolean;
  allLabel?: string;
}

export const CategoryDropdown: React.FC<Props> = ({
  tabs,
  selectedCategories,
  onToggleCategory,
  label,
  isMultiSelect = true,
  allLabel = "All"
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const displayLabel = label || t('documents_explorer.categories');

  return (
    <div className="flex flex-1 items-center gap-2 pb-2 sm:pb-0 min-w-0">
      <div className="relative w-full sm:w-auto">
        <button
          ref={triggerRef}
          onClick={(e) => {
            e.stopPropagation();
            setIsOpen(!isOpen);
          }}
          className={`w-full sm:w-auto flex items-center justify-between px-4 py-2 bg-white dark:bg-dark-surface rounded-xl border transition-all ${!selectedCategories.includes('All') ? 'border-blue-500 ring-2 ring-blue-500/10' : 'border-gray-200 dark:border-dark-border hover:border-blue-200'}`}
        >
          <div className="flex items-center space-x-2">
            <Grid className={`w-4 h-4 ${!selectedCategories.includes('All') ? 'text-blue-500' : 'text-gray-400'}`} />
            <span className="text-sm font-bold text-gray-700 dark:text-dark-text whitespace-nowrap">
              {selectedCategories.includes('All') ? displayLabel : (isMultiSelect ? `${selectedCategories.length} Selected` : selectedCategories[0])}
            </span>
          </div>
          <ChevronDown className={`w-4 h-4 ml-2 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>

        <Popover
          isOpen={isOpen}
          onClose={() => setIsOpen(false)}
          triggerRef={triggerRef}
          side="bottom"
          align="start"
          sideOffset={8}
        >
          <div
            className="w-64 bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200 max-h-[300px] overflow-y-auto custom-scrollbar"
            onClick={(e) => e.stopPropagation()}
          >
            {tabs.map((tab) => (
              <button
                key={tab.id || tab.name}
                onClick={() => onToggleCategory(tab.id || tab.name)}
                className={`w-full flex items-center justify-between px-4 py-3 text-sm font-bold transition-colors ${selectedCategories.includes(tab.id || tab.name) ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600' : 'text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-bg'}`}
              >
                <div className="flex items-center space-x-3">
                  {tab.icon ? (
                    <div className={selectedCategories.includes(tab.id || tab.name) ? '' : ''} style={!selectedCategories.includes(tab.id || tab.name) ? { color: tab.color || 'inherit' } : { color: 'inherit' }}>
                      {React.isValidElement(tab.icon) ? tab.icon : <DynamicIcon icon={tab.icon} className="w-4 h-4" />}
                    </div>
                  ) : (tab.id || tab.name) === 'All' ? (
                    <Grid className="w-4 h-4" />
                  ) : (
                    <div className="w-4 h-4" /> // placeholder
                  )}
                  <span>{(tab.id || tab.name) === 'All' ? allLabel : tab.name}</span>
                </div>
                <div className="flex items-center space-x-2">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full ${selectedCategories.includes(tab.id || tab.name) ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400' : 'bg-gray-100 dark:bg-dark-bg text-gray-400'}`}>
                    {tab.count}
                  </span>
                  {selectedCategories.includes(tab.id || tab.name) && <CheckCircle2 className="w-4 h-4 text-blue-500" />}
                </div>
              </button>
            ))}
          </div>
        </Popover>
      </div>
      
      {/* Active Category Pills (Desktop Only) */}
      <div className="hidden lg:flex flex-wrap items-center gap-2 overflow-hidden max-w-[400px]">
        {selectedCategories.includes('All') ? (
          <span className="px-3 py-1.5 text-xs font-bold bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted rounded-lg">{allLabel} {displayLabel}</span>
        ) : (
          selectedCategories.map(cat => {
            const tab = tabs.find(t => (t.id || t.name) === cat);
            if (!tab) return null;
            return (
              <span key={cat} className="flex items-center space-x-1.5 px-3 py-1.5 text-xs font-bold bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border border-blue-100 dark:border-blue-800 rounded-lg whitespace-nowrap">
                {tab.icon && (React.isValidElement(tab.icon) ? tab.icon : <DynamicIcon icon={tab.icon} className="w-3 h-3" />)}
                <span>{tab.name}</span>
                {isMultiSelect && (
                  <button onClick={(e) => { e.stopPropagation(); onToggleCategory(cat); }} className="hover:bg-blue-100 dark:hover:bg-blue-800 rounded-full p-0.5 transition-colors">
                    <X className="w-3 h-3" />
                  </button>
                )}
              </span>
            )
          })
        )}
      </div>
    </div>
  );
};
