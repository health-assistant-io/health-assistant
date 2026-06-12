import React, { useState, useRef, useEffect } from 'react';
import { Search, ChevronDown, Check, Plus, Activity, Stethoscope, X } from 'lucide-react';
import { Doctor } from '../../services/doctorService';
import { formatUnit } from '../../utils/biomarkerUtils';

interface Props {
  doctors: Doctor[];
  selectedIds: string[];
  onSelect: (id: string) => void;
  onDeselect: (id: string) => void;
  onCreateDoctor: (name: string) => Promise<void>;
  placeholder?: string;
  className?: string;
}

export const DoctorSelector: React.FC<Props> = ({
  doctors,
  selectedIds,
  onSelect,
  onDeselect,
  onCreateDoctor,
  placeholder = "Select Doctors...",
  className = ""
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
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

  const filteredDoctors = doctors.filter(d => 
    d.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
    d.specialty?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const selectedDoctors = doctors.filter(d => selectedIds.includes(d.id));

  const handleCreate = async () => {
    if (!searchTerm.trim()) return;
    setIsCreating(true);
    try {
      // Normalize name: remove Dr. prefix if user typed it
      const normalizedName = searchTerm.replace(/^(dr\.?\s*)+/i, '').trim();
      await onCreateDoctor(normalizedName);
      setSearchTerm('');
      setIsOpen(false);
    } catch (err) {
      console.error("Failed to create doctor", err);
    } finally {
      setIsCreating(false);
    }
  };

  const containerClasses = className.includes('border-none')
    ? "w-full min-h-[46px] px-0 py-2 bg-transparent text-gray-900 dark:text-dark-text cursor-pointer flex flex-wrap gap-2 items-center"
    : "w-full min-h-[46px] px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-gray-900 dark:text-dark-text focus-within:ring-2 focus-within:ring-blue-500/20 cursor-pointer flex flex-wrap gap-2 items-center";

  return (
    <div className={`relative ${className.replace('border-none', '')}`} ref={dropdownRef}>
      <div 
        className={containerClasses}
        onClick={() => setIsOpen(!isOpen)}
      >
        {selectedDoctors.length > 0 ? (
          selectedDoctors.map(doc => (
            <span 
              key={doc.id} 
              className="flex items-center gap-1.5 px-2.5 py-1 bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 rounded-lg text-xs font-bold border border-blue-200 dark:border-blue-800"
              onClick={(e) => {
                e.stopPropagation();
                onDeselect(doc.id);
              }}
            >
              <span>{doc.name.toLowerCase().startsWith('dr') ? doc.name : `Dr. ${doc.name}`}</span>
              <X className="w-3 h-3 hover:text-red-500 transition-colors" />
            </span>
          ))
        ) : (
          <span className="text-gray-400 text-sm">{placeholder}</span>
        )}
        <div className="flex-1" />
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform flex-shrink-0 ${isOpen ? 'rotate-180' : ''}`} />
      </div>

      {isOpen && (
        <div className="absolute z-[210] w-full mt-1 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
          <div className="p-2 border-b border-gray-50 dark:border-dark-border sticky top-0 bg-white dark:bg-dark-surface">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                type="text"
                autoFocus
                placeholder="Search by name or specialty..."
                className="w-full pl-9 pr-4 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-md text-sm outline-none focus:ring-1 focus:ring-blue-500 dark:text-dark-text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>
          
          <div className="max-h-60 overflow-y-auto custom-scrollbar">
            {filteredDoctors.length > 0 ? (
              filteredDoctors.map((doc) => {
                const isSelected = selectedIds.includes(doc.id);
                return (
                  <div
                    key={doc.id}
                    className={`px-4 py-2.5 text-sm flex items-center justify-between cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors ${isSelected ? 'bg-blue-50/50 dark:bg-blue-900/10' : ''}`}
                    onClick={() => {
                      if (isSelected) onDeselect(doc.id);
                      else onSelect(doc.id);
                      setSearchTerm('');
                    }}
                  >
                    <div className="flex items-center gap-3">
                      <div className={`p-1.5 rounded-lg ${isSelected ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-400 dark:bg-dark-bg'}`}>
                        <Stethoscope className="w-3.5 h-3.5" />
                      </div>
                      <div className="flex flex-col">
                        <span className={`font-bold ${isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-gray-700 dark:text-dark-text'}`}>
                          {doc.name.toLowerCase().startsWith('dr') ? doc.name : `Dr. ${doc.name}`}
                        </span>
                        {doc.specialty && <span className="text-[10px] text-gray-400 uppercase tracking-tighter">{doc.specialty}</span>}
                      </div>
                    </div>
                    {isSelected && <Check className="w-4 h-4 text-blue-600" />}
                  </div>
                );
              })
            ) : searchTerm.trim() ? (
              <div
                className="px-4 py-4 text-sm text-blue-600 dark:text-blue-400 font-bold cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors flex items-center gap-2 border-t border-gray-50 dark:border-dark-border"
                onClick={handleCreate}
              >
                {isCreating ? <Activity className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                <div className="flex flex-col">
                   <span className="text-xs uppercase tracking-widest">Not found</span>
                   <span className="text-sm">Create "Dr. {searchTerm.replace(/^(dr\.?\s*)+/i, '').trim()}"</span>
                </div>
              </div>
            ) : (
              <div className="px-4 py-6 text-sm text-gray-400 italic text-center">
                <Stethoscope className="w-8 h-8 mx-auto mb-2 opacity-20" />
                <p>Type to search clinical staff...</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
