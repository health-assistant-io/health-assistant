import React, { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Search, CheckCircle, Filter } from 'lucide-react';
import { ExaminationCard } from './ExaminationCard';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  examinations: any[];
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  title?: string;
}

export const ExaminationSelectorModal: React.FC<Props> = ({ 
  isOpen, 
  onClose, 
  examinations, 
  selectedIds, 
  onSelectionChange,
  title
}) => {
  const { t } = useTranslation();
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTab, setActiveTab] = useState('All');

  const categories = useMemo(() => {
    const cats = new Set(examinations.map(e => e.category_details?.name || e.category || 'Other'));
    return ['All', ...Array.from(cats).sort()];
  }, [examinations]);

  const filteredExaminations = examinations.filter(e => {
    const category = e.category_details?.name || e.category || 'Other';
    const notes = (e.notes || '').toLowerCase();
    const patientNotes = (e.patient_notes || '').toLowerCase();
    const doctorNames = (e.doctors || []).map((d: any) => d.name.toLowerCase()).join(' ');
    const searchLower = searchTerm.toLowerCase();

    const matchesSearch = notes.includes(searchLower) || patientNotes.includes(searchLower) || doctorNames.includes(searchLower);
    const matchesTab = activeTab === 'All' || category === activeTab;

    return matchesSearch && matchesTab;
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
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-md animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-[2.5rem] shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between bg-white dark:bg-dark-surface">
          <div>
            <h2 className="text-xl font-black text-gray-900 dark:text-dark-text tracking-tight uppercase">
              {title || t('events.select_related_visits')}
            </h2>
            <p className="text-[10px] text-gray-400 font-black uppercase tracking-widest mt-0.5">
              {selectedIds.length} visits selected
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
                onClick={() => setActiveTab(cat)}
                className={`whitespace-nowrap px-4 py-1.5 text-[10px] font-black uppercase tracking-widest rounded-xl transition-all ${
                  activeTab === cat 
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
        <div className="flex-1 overflow-y-auto pl-8 pr-10 py-6 space-y-4 custom-scrollbar">
          {filteredExaminations.length === 0 ? (
            <div className="py-20 text-center">
              <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-4">
                <Search className="w-8 h-8 text-gray-300" />
              </div>
              <p className="text-sm text-gray-400 font-bold uppercase tracking-widest">{t('common.no_results')}</p>
            </div>
          ) : (
            <div className="relative border-l-2 border-blue-50 dark:border-blue-900/20 space-y-4 ml-6">
              {filteredExaminations.map(exam => (
                <ExaminationCard 
                  key={exam.id}
                  examination={exam}
                  isSelected={selectedIds.includes(exam.id)}
                  isSelectable={true}
                  onSelectToggle={toggleSelect}
                  onClick={() => toggleSelect(exam.id)}
                  showExternalLink={true}
                  allowEventInteraction={false}
                  className="transition-all active:scale-[0.98]"
                />
              ))}
            </div>
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
