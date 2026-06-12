import React, { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Search, CheckCircle, Filter, Activity } from 'lucide-react';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  observations: any[];
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  title?: string;
}

export const ObservationSelectorModal: React.FC<Props> = ({ 
  isOpen, 
  onClose, 
  observations, 
  selectedIds, 
  onSelectionChange,
  title
}) => {
  const { t } = useTranslation();
  const [searchTerm, setSearchTerm] = useState('');
  const [activeCategory, setActiveCategory] = useState('All');

  const categories = useMemo(() => {
    const cats = new Set(observations.map(o => o.category?.[0]?.coding?.[0]?.display || o.category || 'Other'));
    return ['All', ...Array.from(cats).sort()];
  }, [observations]);

  const filteredObservations = observations.filter(o => {
    const name = (o.code?.text || o.code?.coding?.[0]?.display || o.biomarker_slug || '').toLowerCase();
    const category = o.category?.[0]?.coding?.[0]?.display || o.category || 'Other';
    const searchLower = searchTerm.toLowerCase();

    const matchesSearch = name.includes(searchLower);
    const matchesCategory = activeCategory === 'All' || category === activeCategory;

    return matchesSearch && matchesCategory;
  });

  const toggleSelect = (id: string) => {
    if (selectedIds.includes(id)) {
      onSelectionChange(selectedIds.filter(i => i !== id));
    } else {
      onSelectionChange([...selectedIds, id]);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[1100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-md animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-[2.5rem] shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-white dark:bg-dark-surface">
          <div>
            <h2 className="text-xl font-black text-gray-900 dark:text-dark-text tracking-tight uppercase">
              {title || t('events.select_related_biomarkers')}
            </h2>
            <p className="text-[10px] text-gray-400 font-black uppercase tracking-widest mt-0.5">
              {selectedIds.length} biomarkers selected
            </p>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        {/* Search & Filter */}
        <div className="p-6 space-y-4 border-b border-gray-50 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/30">
          <div className="relative group">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 group-focus-within:text-blue-500 transition-colors" />
            <input 
              type="text" 
              placeholder={t('common.search_placeholder')}
              className="w-full pl-12 pr-4 py-3 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl text-sm focus:ring-4 focus:ring-blue-500/10 outline-none transition-all"
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
            />
          </div>

          <div className="flex items-center space-x-2 overflow-x-auto pb-2 no-scrollbar">
            <Filter className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
            {categories.map(cat => (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className={`whitespace-nowrap px-4 py-1.5 text-[10px] font-black uppercase tracking-widest rounded-xl transition-all ${
                  activeCategory === cat 
                    ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20' 
                    : 'bg-white dark:bg-dark-bg text-gray-400 hover:text-blue-600'
                }`}
              >
                {cat === 'All' ? t('common.view_all') : cat}
              </button>
            ))}
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-8 py-6 space-y-3 custom-scrollbar">
          {filteredObservations.length === 0 ? (
            <div className="py-20 text-center">
              <p className="text-sm text-gray-400 font-bold uppercase tracking-widest">{t('common.no_results')}</p>
            </div>
          ) : (
            filteredObservations.map(obs => (
              <button
                key={obs.id}
                onClick={() => toggleSelect(obs.id)}
                className={`w-full flex items-center justify-between p-4 rounded-2xl border transition-all ${
                  selectedIds.includes(obs.id)
                    ? 'bg-blue-50 dark:bg-blue-900/10 border-blue-200 dark:border-blue-800 ring-1 ring-blue-100'
                    : 'bg-gray-50 dark:bg-dark-bg border-transparent hover:border-gray-200'
                }`}
              >
                <div className="flex items-center space-x-4 text-left">
                  <div className={`p-2 rounded-xl ${selectedIds.includes(obs.id) ? 'bg-blue-600 text-white' : 'bg-white dark:bg-dark-surface text-gray-400 shadow-sm'}`}>
                    <Activity className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">
                      {obs.code?.text || obs.code?.coding?.[0]?.display || obs.biomarker_slug || 'Unknown'}
                    </h4>
                    <p className="text-[10px] text-gray-500 font-bold uppercase mt-0.5">
                      {new Date(obs.effective_datetime || obs.effectiveDateTime).toLocaleDateString()} • {obs.raw_value || obs.valueQuantity?.value} {obs.normalized_unit || obs.valueQuantity?.unit}
                    </p>
                  </div>
                </div>
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center transition-all ${
                  selectedIds.includes(obs.id)
                    ? 'bg-blue-600 border-blue-600'
                    : 'bg-white dark:bg-dark-surface border-gray-200'
                }`}>
                  {selectedIds.includes(obs.id) && <CheckCircle className="w-3 h-3 text-white" />}
                </div>
              </button>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="p-6 border-t border-gray-50 dark:border-dark-border bg-white dark:bg-dark-surface flex justify-end">
          <button
            onClick={onClose}
            className="px-8 py-3 bg-blue-600 text-white rounded-2xl font-black text-xs uppercase tracking-widest hover:bg-blue-700 transition-all shadow-xl shadow-blue-500/20 active:scale-95 flex items-center space-x-2"
          >
            <CheckCircle className="w-4 h-4" />
            <span>{t('common.done')}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
