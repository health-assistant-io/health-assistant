import React, { useState, useRef, useEffect } from 'react';
import { Search, ChevronDown, Check, Plus, Activity, Target } from 'lucide-react';
import { BodyPart, listBodyParts, createBodyPart } from '../../services/bodyPartService';

interface Props {
  selectedId?: string;
  onSelect: (bodyPart: BodyPart) => void;
  placeholder?: string;
  className?: string;
  innerClassName?: string;
}

export const BodyPartSelector: React.FC<Props> = ({
  selectedId,
  onSelect,
  placeholder = "Select Body Part...",
  className = "",
  innerClassName = "px-4 py-3"
}) => {
  const [bodyParts, setBodyParts] = useState<BodyPart[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const fetchBodyParts = async () => {
    setIsLoading(true);
    try {
      const data = await listBodyParts();
      setBodyParts(data);
    } catch (err) {
      console.error("Failed to fetch body parts", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchBodyParts();
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const filteredParts = bodyParts.filter(p =>
    p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    p.slug.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const selectedPart = bodyParts.find(p => p.id === selectedId);

  const handleCreate = async () => {
    if (!searchTerm.trim()) return;
    setIsCreating(true);
    try {
      const newPart = await createBodyPart({
        name: searchTerm
      });
      setBodyParts(prev => [...prev, newPart]);
      onSelect(newPart);
      setIsOpen(false);
      setSearchTerm('');
    } catch (err) {
      console.error("Failed to create body part", err);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className={`relative ${className}`} ref={dropdownRef}>
      <div
        className={`w-full ${innerClassName} bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 cursor-pointer flex items-center justify-between transition-all hover:bg-white dark:hover:bg-dark-surface`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center space-x-2">
          <Target className={`w-4 h-4 ${selectedPart ? 'text-blue-500' : 'text-gray-400'}`} />
          <span className={selectedPart ? "text-gray-900 dark:text-dark-text font-bold" : "text-gray-400"}>
            {selectedPart ? selectedPart.name : placeholder}
          </span>
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
                placeholder="Search or add part..."
                className="w-full pl-10 pr-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>

          <div className="max-h-64 overflow-y-auto custom-scrollbar">
            {isLoading ? (
              <div className="p-8 flex justify-center">
                <Activity className="w-6 h-6 text-blue-500 animate-spin" />
              </div>
            ) : filteredParts.length > 0 ? (
              filteredParts.map((p) => (
                <div
                  key={p.id}
                  className={`px-4 py-3 text-sm flex items-center justify-between cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors ${selectedId === p.id ? 'bg-blue-50 dark:bg-blue-900/10 text-blue-600 dark:text-blue-400 font-bold' : 'text-gray-700 dark:text-dark-text'}`}
                  onClick={() => {
                    onSelect(p);
                    setIsOpen(false);
                    setSearchTerm('');
                  }}
                >
                  <div className="flex items-center space-x-2">
                    <span>{p.name}</span>
                    {p.snomed_code && <span className="text-[9px] bg-gray-100 dark:bg-dark-bg px-1.5 py-0.5 rounded text-gray-400 font-medium">SNOMED: {p.snomed_code}</span>}
                  </div>
                  {selectedId === p.id && <Check className="w-4 h-4" />}
                </div>
              ))
            ) : searchTerm.trim() ? (
              <div
                className="px-4 py-4 text-sm text-blue-600 dark:text-blue-400 font-bold cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors flex items-center gap-2"
                onClick={handleCreate}
              >
                {isCreating ? <Activity className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                <span>Create "{searchTerm}"</span>
              </div>
            ) : (
              <div className="px-4 py-8 text-sm text-gray-400 italic text-center">
                Type to find or add a body part...
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
