import React, { useState, useRef } from 'react';
import { Search, ChevronDown, Check, Plus, Activity, Bookmark, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DynamicIcon } from './DynamicIcon';
import { Popover } from './Popover';

interface Category {
  id: string;
  name: string;
  slug?: string;
  icon?: string;
  color?: string;
}

interface Props {
  categories: Category[];
  selectedName: string;
  onSelect: (name: string) => void;
  onCreate?: (name: string) => Promise<void>;
  placeholder?: string;
  className?: string;
}

export const CategorySelector: React.FC<Props> = ({
  categories,
  selectedName,
  onSelect,
  onCreate,
  placeholder = "Select Category...",
  className = ""
}) => {
  const { t } = useTranslation();
  const [searchTerm, setSearchTerm] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);

  const filteredCats = categories.filter(c =>
    c.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const selectedCategory = categories.find(c => c.name === selectedName);

  const handleCreate = async () => {
    if (!searchTerm.trim() || !onCreate) return;
    setIsCreating(true);
    try {
      await onCreate(searchTerm.trim());
      setSearchTerm('');
      setIsOpen(false);
    } catch (err) {
      console.error("Failed to create category", err);
    } finally {
      setIsCreating(false);
    }
  };

  const containerClasses = className.includes('border-none') 
    ? "w-full min-h-[40px] py-2 bg-transparent text-gray-900 dark:text-dark-text cursor-pointer flex gap-2 items-center"
    : "w-full min-h-[46px] px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-gray-900 dark:text-dark-text focus-within:ring-2 focus-within:ring-blue-500/20 cursor-pointer flex gap-2 items-center";

  return (
    <div className={`relative ${className.replace('border-none', '')}`}>
      <div
        ref={triggerRef}
        className={containerClasses}
        onClick={() => setIsOpen(!isOpen)}
      >
        {selectedName ? (
          <div className="flex-1 flex items-center justify-between">
            <div className="flex items-center gap-2">
               {selectedCategory?.icon && (
                  <div className="text-blue-500 dark:text-blue-400">
                     <DynamicIcon icon={selectedCategory.icon} className="w-3.5 h-3.5" />
                  </div>
               )}
               <span className="text-sm font-bold text-gray-900 dark:text-dark-text">
                 {selectedName}
               </span>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSelect('');
              }}
              className="p-1 hover:bg-gray-100 dark:hover:bg-blue-900/40 rounded-full"
            >
              <X className="w-3 h-3 text-gray-400" />
            </button>
          </div>
        ) : (
          <span className="text-gray-400 text-sm flex-1">{placeholder}</span>
        )}
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
        <div className="w-full bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200" style={{ minWidth: 240 }}>
          <div className="p-2 border-b border-gray-50 dark:border-dark-border sticky top-0 bg-white dark:bg-dark-surface">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                type="text"
                autoFocus
                placeholder="Search categories..."
                className="w-full pl-9 pr-4 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-md text-sm outline-none focus:ring-1 focus:ring-blue-500 dark:text-dark-text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>

          <div className="max-h-60 overflow-y-auto custom-scrollbar">
            {filteredCats.length > 0 ? (
              filteredCats.map((cat) => {
                const isSelected = selectedName === cat.name;
                return (
                  <div
                    key={cat.id || cat.slug}
                    className={`px-4 py-2.5 text-sm flex items-center justify-between cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors ${isSelected ? 'bg-blue-50/50 dark:bg-blue-900/10' : ''}`}
                    onClick={() => {
                      onSelect(cat.name);
                      setIsOpen(false);
                      setSearchTerm('');
                    }}
                  >
                    <div className="flex items-center gap-3">
                      <div className={`p-1.5 rounded-lg ${isSelected ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-400 dark:bg-dark-bg'}`}>
                        {cat.icon ? <DynamicIcon icon={cat.icon} className="w-3.5 h-3.5" /> : <Bookmark className="w-3.5 h-3.5" />}
                      </div>
                      <span className={`font-bold ${isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-gray-700 dark:text-dark-text'}`}>
                        {cat.name}
                      </span>
                    </div>
                    {isSelected && <Check className="w-4 h-4 text-blue-600" />}
                  </div>
                );
              })
            ) : (searchTerm.trim() && onCreate) ? (
              <div
                className="px-4 py-4 text-sm text-blue-600 dark:text-blue-400 font-bold cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors flex items-center gap-2 border-t border-gray-50 dark:border-dark-border"
                onClick={handleCreate}
              >
                {isCreating ? <Activity className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                <div className="flex flex-col">
                   <span className="text-xs uppercase tracking-widest">Not found</span>
                   <span className="text-sm">Create "{searchTerm.trim()}"</span>
                </div>
              </div>
            ) : (
              <div className="px-4 py-6 text-sm text-gray-400 italic text-center">
                <Bookmark className="w-8 h-8 mx-auto mb-2 opacity-20" />
                <p>No categories found...</p>
              </div>
            )}
          </div>
        </div>
      </Popover>
    </div>
  );
};
