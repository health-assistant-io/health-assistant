import React, { useState, useRef } from 'react';
import { Search, ChevronDown, Check, Plus, Activity } from 'lucide-react';
import { Unit } from '../../types/biomarker';
import biomarkerService from '../../services/biomarkerService';
import { formatUnit } from '../../utils/biomarkerUtils';
import { Popover } from './Popover';

interface Props {
  units: Unit[];
  selectedId?: string;
  selectedSymbol?: string;
  onSelect: (unit: Unit) => void;
  onUnitsUpdated: (newUnits: Unit[]) => void;
  placeholder?: string;
  className?: string;
}

export const UnitSelector: React.FC<Props> = ({
  units,
  selectedId,
  selectedSymbol,
  onSelect,
  onUnitsUpdated,
  placeholder = "Select Unit...",
  className = ""
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);

  const filteredUnits = units.filter(u =>
    u.symbol.toLowerCase().includes(searchTerm.toLowerCase()) ||
    u.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const selectedUnit = selectedId 
    ? units.find(u => u.id === selectedId)
    : units.find(u => u.symbol === selectedSymbol);

  const handleCreate = async () => {
    if (!searchTerm.trim()) return;
    setIsCreating(true);
    try {
      const newUnit = await biomarkerService.createUnit({
        symbol: searchTerm,
        name: searchTerm,
        quantity_type: 'other'
      });
      const updatedUnits = [...units, newUnit];
      onUnitsUpdated(updatedUnits);
      onSelect(newUnit);
      setIsOpen(false);
      setSearchTerm('');
    } catch (err) {
      console.error("Failed to create unit", err);
      alert("Failed to create unit. It might already exist.");
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className={`relative ${className}`}>
      <div
        ref={triggerRef}
        className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 cursor-pointer flex items-center justify-between"
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className={selectedUnit ? "text-gray-900 dark:text-dark-text font-bold" : "text-gray-400"}>
          {selectedUnit ? formatUnit(selectedUnit.symbol) : placeholder}
        </span>
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </div>

      <Popover
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        triggerRef={triggerRef}
        side="bottom"
        align="start"
        sideOffset={4}
      >
        <div className="w-full bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200" style={{ minWidth: 220 }}>
          <div className="p-2 border-b border-gray-50 dark:border-dark-border sticky top-0 bg-white dark:bg-dark-surface">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                type="text"
                autoFocus
                placeholder="Search or create unit..."
                className="w-full pl-9 pr-4 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-md text-sm outline-none focus:ring-1 focus:ring-blue-500"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>

          <div className="max-h-48 overflow-y-auto custom-scrollbar">
            {filteredUnits.length > 0 ? (
              filteredUnits.map((u) => (
                <div
                  key={u.id}
                  className={`px-4 py-2 text-sm flex items-center justify-between cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors ${selectedUnit?.id === u.id ? 'bg-blue-50 dark:bg-blue-900/10 text-blue-600 dark:text-blue-400 font-bold' : 'text-gray-700 dark:text-dark-text'}`}
                  onClick={() => {
                    onSelect(u);
                    setIsOpen(false);
                    setSearchTerm('');
                  }}
                >
                  <span>{formatUnit(u.symbol)} {u.name !== u.symbol ? <span className="text-[10px] opacity-50 ml-1">({u.name})</span> : ''}</span>
                  {selectedUnit?.id === u.id && <Check className="w-3.5 h-3.5" />}
                </div>
              ))
            ) : searchTerm.trim() ? (
              <div
                className="px-4 py-3 text-sm text-blue-600 dark:text-blue-400 font-bold cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors flex items-center gap-2"
                onClick={handleCreate}
              >
                {isCreating ? <Activity className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                <span>Create "{searchTerm}"</span>
              </div>
            ) : (
              <div className="px-4 py-3 text-sm text-gray-400 italic text-center">
                Type to search...
              </div>
            )}
          </div>
        </div>
      </Popover>
    </div>
  );
};
