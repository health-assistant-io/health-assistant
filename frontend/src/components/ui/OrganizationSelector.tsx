import React, { useState, useRef, useEffect } from 'react';
import { Search, ChevronDown, Check, Plus, Activity, Building2, X } from 'lucide-react';
import { Organization } from '../../services/organizationService';
import { useTranslation } from 'react-i18next';

interface Props {
  organizations: Organization[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onCreate?: (name: string) => Promise<void>;
  placeholder?: string;
  className?: string;
}

export const OrganizationSelector: React.FC<Props> = ({
  organizations,
  selectedId,
  onSelect,
  onCreate,
  placeholder = "Select Facility...",
  className = ""
}) => {
  const { t } = useTranslation();
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

  const filteredOrgs = organizations.filter(o => 
    o.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
    (o.type?.[0]?.coding?.[0]?.display || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  const selectedOrg = organizations.find(o => o.id === selectedId);

  const handleCreate = async () => {
    if (!searchTerm.trim() || !onCreate) return;
    setIsCreating(true);
    try {
      await onCreate(searchTerm.trim());
      setSearchTerm('');
      setIsOpen(false);
    } catch (err) {
      console.error("Failed to create organization", err);
    } finally {
      setIsCreating(false);
    }
  };

  const containerClasses = className.includes('border-none') 
    ? "w-full min-h-[46px] px-0 py-2 bg-transparent text-gray-900 dark:text-dark-text cursor-pointer flex gap-2 items-center"
    : "w-full min-h-[46px] px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-gray-900 dark:text-dark-text focus-within:ring-2 focus-within:ring-blue-500/20 cursor-pointer flex gap-2 items-center";

  return (
    <div className={`relative ${className.replace('border-none', '')}`} ref={dropdownRef}>
      <div 
        className={containerClasses}
        onClick={() => setIsOpen(!isOpen)}
      >
        {selectedOrg ? (
          <div className="flex-1 flex items-center justify-between">
            <span className="text-sm font-bold text-blue-700 dark:text-blue-300">
              {selectedOrg.name}
            </span>
            <button 
              onClick={(e) => {
                e.stopPropagation();
                onSelect(null);
              }}
              className="p-1 hover:bg-blue-100 dark:hover:bg-blue-900/40 rounded-full"
            >
              <X className="w-3 h-3 text-blue-500" />
            </button>
          </div>
        ) : (
          <span className="text-gray-400 text-sm flex-1">{placeholder}</span>
        )}
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
                placeholder="Search facilities..."
                className="w-full pl-9 pr-4 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-md text-sm outline-none focus:ring-1 focus:ring-blue-500 dark:text-dark-text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>
          
          <div className="max-h-60 overflow-y-auto custom-scrollbar">
            {filteredOrgs.length > 0 ? (
              filteredOrgs.map((org) => {
                const isSelected = selectedId === org.id;
                return (
                  <div
                    key={org.id}
                    className={`px-4 py-2.5 text-sm flex items-center justify-between cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors ${isSelected ? 'bg-blue-50/50 dark:bg-blue-900/10' : ''}`}
                    onClick={() => {
                      onSelect(org.id);
                      setIsOpen(false);
                      setSearchTerm('');
                    }}
                  >
                    <div className="flex items-center gap-3">
                      <div className={`p-1.5 rounded-lg ${isSelected ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-400 dark:bg-dark-bg'}`}>
                        <Building2 className="w-3.5 h-3.5" />
                      </div>
                      <div className="flex flex-col">
                        <span className={`font-bold ${isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-gray-700 dark:text-dark-text'}`}>
                          {org.name}
                        </span>
                        <span className="text-[10px] text-gray-400 uppercase tracking-tighter">
                          {org.type?.[0]?.coding?.[0]?.display || 'Medical Facility'}
                        </span>
                      </div>
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
                <Building2 className="w-8 h-8 mx-auto mb-2 opacity-20" />
                <p>No facilities found...</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
