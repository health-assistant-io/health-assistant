import React from 'react';
import { Search, LayoutGrid, List as ListIcon, Plus, Filter } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ClinicalEventCategory } from '../../services/clinicalEventService';
import { getEventIcon } from '../../utils/clinicalEventUtils';

interface Props {
  searchTerm: string;
  setSearchTerm: (val: string) => void;
  viewMode: 'grid' | 'list';
  setViewMode: (mode: 'grid' | 'list') => void;
  activeCategoryId: string;
  setActiveCategoryId: (id: string) => void;
  categories: ClinicalEventCategory[];
  onAddEvent: () => void;
}

export const EventFilterToolbar: React.FC<Props> = ({
  searchTerm,
  setSearchTerm,
  viewMode,
  setViewMode,
  activeCategoryId,
  setActiveCategoryId,
  categories,
  onAddEvent
}) => {
  const { t } = useTranslation();

  return (
    <div className="bg-white dark:bg-dark-surface p-4 rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm space-y-4">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="relative group flex-1 max-w-md">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 group-focus-within:text-blue-500 transition-colors" />
          <input 
            type="text" 
            placeholder={t('events.search_events_placeholder')}
            className="w-full pl-11 pr-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-transparent rounded-xl text-sm focus:ring-2 focus:ring-blue-500/20 outline-none transition-all"
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>
        <div className="flex items-center space-x-3">
           <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-xl">
              <button 
                onClick={() => setViewMode('grid')}
                className={`p-2 rounded-lg transition-all ${viewMode === 'grid' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400'}`}
                title="Grid View"
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button 
                onClick={() => setViewMode('list')}
                className={`p-2 rounded-lg transition-all ${viewMode === 'list' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400'}`}
                title="List View"
              >
                <ListIcon className="w-4 h-4" />
              </button>
           </div>
           <button
            onClick={onAddEvent}
            className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 font-bold active:scale-95 text-xs uppercase tracking-widest"
          >
            <Plus className="w-4 h-4" />
            <span>{t('events.add_event')}</span>
          </button>
        </div>
      </div>

      <div className="flex items-center space-x-2 overflow-x-auto no-scrollbar pb-1">
        <Filter className="w-3.5 h-3.5 text-gray-400 mr-2 flex-shrink-0" />
        <button
          onClick={() => setActiveCategoryId('All')}
          className={`whitespace-nowrap px-4 py-1.5 text-[10px] font-black uppercase tracking-widest rounded-full transition-all border ${
            activeCategoryId === 'All' 
              ? 'bg-blue-600 border-blue-600 text-white shadow-md' 
              : 'bg-white dark:bg-dark-surface text-gray-500 border-gray-100 dark:border-dark-border hover:border-blue-100'
          }`}
        >
          {t('common.view_all')}
        </button>
        {categories.map(cat => (
          <button
            key={cat.id}
            onClick={() => setActiveCategoryId(cat.id)}
            className={`whitespace-nowrap flex items-center space-x-2 px-4 py-1.5 text-[10px] font-black uppercase tracking-widest rounded-full transition-all border ${
              activeCategoryId === cat.id 
                ? 'bg-blue-600 border-blue-600 text-white shadow-md' 
                : 'bg-white dark:bg-dark-surface text-gray-500 border-gray-100 dark:border-dark-border hover:border-blue-100'
            }`}
          >
            <span className={activeCategoryId === cat.id ? 'text-white' : ''} style={activeCategoryId !== cat.id ? { color: cat.color } : undefined}>
              {getEventIcon(cat.slug, "w-3 h-3")}
            </span>
            <span>{cat.name}</span>
          </button>
        ))}
      </div>
    </div>
  );
};
