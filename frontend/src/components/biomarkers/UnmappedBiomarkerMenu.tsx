import React, { useState, useRef, useEffect } from 'react';
import { Zap, Plus, Link2, Search, X, Check, Activity } from 'lucide-react';
import { toast } from 'react-toastify';
import biomarkerService from '../../services/biomarkerService';
import { CreateBiomarkerModal } from '../examinations/CreateBiomarkerModal';
import { filterBiomarkers } from '../../utils/searchUtils';
import { Biomarker } from '../../types/biomarker';
import { refreshBiomarkerDefinitions } from '../../hooks/useBiomarkers';

interface UnmappedBiomarkerMenuProps {
  rawName: string;
  patientId?: string;
  onRemapped?: () => void;
}

/**
 * Affordance shown next to an unmapped biomarker (an observation with no
 * matching definition). Offers two actions via a popup:
 *   1. "Create biomarker" — opens the create-definition modal prefilled with
 *      the raw name, then auto-relinks the observations to the new definition.
 *   2. "Map to existing" — pick an existing definition from a searchable list,
 *      then relinks the observations.
 */
export const UnmappedBiomarkerMenu: React.FC<UnmappedBiomarkerMenuProps> = ({
  rawName,
  patientId,
  onRemapped,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [mode, setMode] = useState<'menu' | 'map'>('menu');
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [definitions, setDefinitions] = useState<Biomarker[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [selected, setSelected] = useState<Biomarker | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const [loadingDefs, setLoadingDefs] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setMode('menu');
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const reset = () => {
    setIsOpen(false);
    setMode('menu');
    setSelected(null);
    setSearchTerm('');
  };

  const handleCreateClick = () => {
    setIsCreateOpen(true);
    reset();
  };

  const handleCreateSuccess = async (newBiomarker: Biomarker) => {
    // Auto-relink the unmapped observations to the freshly-created definition
    setIsWorking(true);
    try {
      const res = await biomarkerService.remapObservations(
        newBiomarker.id,
        rawName,
        patientId
      );
      toast.success(
        `Created "${newBiomarker.name}" and linked ${res.observations_remapped} result(s).`
      );
      refreshBiomarkerDefinitions();
      onRemapped?.();
    } catch (err) {
      console.error('Auto-remap after create failed', err);
      toast.warn(
        `Definition created, but existing results could not be linked automatically. They will link on future imports.`
      );
      onRemapped?.();
    } finally {
      setIsWorking(false);
    }
  };

  const openMapMode = async () => {
    setMode('map');
    if (definitions.length === 0 && !loadingDefs) {
      setLoadingDefs(true);
      try {
        const defs = await biomarkerService.getAllBiomarkers();
        setDefinitions(defs || []);
      } catch (e) {
        console.error('Failed to load definitions', e);
        toast.error('Could not load biomarker definitions.');
      } finally {
        setLoadingDefs(false);
      }
    }
  };

  const handleConfirmMap = async () => {
    if (!selected) return;
    setIsWorking(true);
    try {
      const res = await biomarkerService.remapObservations(
        selected.id,
        rawName,
        patientId
      );
      toast.success(
        `Linked ${res.observations_remapped} result(s) to "${selected.name}".`
      );
      refreshBiomarkerDefinitions();
      onRemapped?.();
      reset();
    } catch (err) {
      console.error('Remap failed', err);
      toast.error('Failed to link results to the selected definition.');
    } finally {
      setIsWorking(false);
    }
  };

  const filteredDefs = filterBiomarkers(definitions, searchTerm);

  return (
    <>
      <div className="relative" ref={containerRef}>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setIsOpen(!isOpen);
          }}
          title="Define or map this biomarker"
          aria-label="Define or map this biomarker"
          className="p-1.5 text-amber-500 hover:text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-900/20 rounded-md transition-colors nodrag"
        >
          {isWorking ? (
            <Activity className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Zap className="w-3.5 h-3.5" />
          )}
        </button>

        {isOpen && mode === 'menu' && (
          <div className="absolute top-full right-0 mt-1.5 z-[200] w-60 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-top-1 duration-200">
            <div className="px-4 py-3 border-b border-gray-100 dark:border-dark-border">
              <p className="text-[9px] font-black uppercase tracking-widest text-amber-500 flex items-center gap-1.5">
                <Zap className="w-3 h-3" />
                Unmapped Biomarker
              </p>
              <p className="text-xs text-gray-500 dark:text-dark-muted mt-1 truncate">
                "{rawName}" has no definition
              </p>
            </div>
            <div className="p-1.5">
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); handleCreateClick(); }}
                className="w-full text-left px-3 py-2.5 rounded-xl text-xs font-bold text-gray-700 dark:text-dark-text hover:bg-blue-50 dark:hover:bg-blue-900/30 hover:text-blue-600 transition-colors flex items-center gap-2.5"
              >
                <Plus className="w-4 h-4 text-blue-500" />
                <div className="flex flex-col">
                  <span>Create biomarker</span>
                  <span className="text-[9px] font-medium text-gray-400 normal-case">New definition from this name</span>
                </div>
              </button>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); openMapMode(); }}
                className="w-full text-left px-3 py-2.5 rounded-xl text-xs font-bold text-gray-700 dark:text-dark-text hover:bg-emerald-50 dark:hover:bg-emerald-900/30 hover:text-emerald-600 transition-colors flex items-center gap-2.5"
              >
                <Link2 className="w-4 h-4 text-emerald-500" />
                <div className="flex flex-col">
                  <span>Map to existing</span>
                  <span className="text-[9px] font-medium text-gray-400 normal-case">Link to a definition</span>
                </div>
              </button>
            </div>
          </div>
        )}

        {isOpen && mode === 'map' && (
          <div className="absolute top-full right-0 mt-1.5 z-[200] w-72 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in slide-in-from-top-1 duration-200">
            <div className="px-4 py-3 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
              <p className="text-[9px] font-black uppercase tracking-widest text-gray-400">Map to existing</p>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); reset(); }}
                aria-label="Close map panel"
                className="p-1.5 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full"
              >
                <X className="w-3.5 h-3.5 text-gray-400" />
              </button>
            </div>
            <div className="p-2 border-b border-gray-100 dark:border-dark-border">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                <input
                  type="text"
                  className="w-full pl-8 pr-3 py-2 text-xs bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-lg outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 dark:text-dark-text"
                  placeholder="Search definitions..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  autoFocus
                />
              </div>
            </div>
            <div className="py-1.5 max-h-56 overflow-y-auto custom-scrollbar">
              {loadingDefs ? (
                <div className="px-4 py-3 text-[10px] text-gray-400 italic">Loading...</div>
              ) : filteredDefs.length === 0 ? (
                <div className="px-4 py-3 text-[10px] text-gray-400 italic">No definitions found</div>
              ) : (
                filteredDefs.map((opt) => {
                  const isSelected = selected?.id === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setSelected(opt); }}
                      className={`w-full text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider transition-all flex items-center justify-between ${isSelected ? 'bg-blue-600 text-white' : 'text-gray-600 dark:text-dark-text hover:bg-blue-50 dark:hover:bg-blue-900/40'}`}
                    >
                      <span className="truncate pr-2">{opt.name}</span>
                      {isSelected && <Check className="w-3 h-3 flex-shrink-0" />}
                    </button>
                  );
                })
              )}
            </div>
            {selected && (
              <div className="p-3 border-t border-gray-100 dark:border-dark-border">
                <button
                  type="button"
                  disabled={isWorking}
                  onClick={(e) => { e.stopPropagation(); handleConfirmMap(); }}
                  className="w-full px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white rounded-xl font-bold text-[10px] uppercase tracking-widest transition-all flex items-center justify-center gap-2"
                >
                  {isWorking ? <Activity className="w-3.5 h-3.5 animate-spin" /> : <Link2 className="w-3.5 h-3.5" />}
                  Link to "{selected.name}"
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <CreateBiomarkerModal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        onSuccess={handleCreateSuccess}
        initialName={rawName}
      />
    </>
  );
};
