import React, { useState, useEffect } from 'react';
import { X, Save, ListTree, Tag, Info, Activity, Plus, Sparkles, Search, ChevronDown, Check } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { UnitSelector } from '../ui/UnitSelector';
import biomarkerService from '../../services/biomarkerService';
import { Unit } from '../../types/biomarker';
import { formatUnit } from '../../utils/biomarkerUtils';
import { RichTextEditor } from '../ui/RichTextEditor';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (newBiomarker: any) => void;
  initialName?: string;
}

export const CreateBiomarkerModal: React.FC<Props> = ({ 
  isOpen, 
  onClose, 
  onSuccess,
  initialName = ''
}) => {
  const [units, setUnits] = useState<Unit[]>([]);
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({
    name: initialName,
    slug: '',
    coding_system: 'loinc',
    code: '',
    category: '',
    aliases: [] as string[],
    preferred_unit_id: '',
    reference_range_min: '',
    reference_range_max: '',
    info: ''
  });
  const [aliasInput, setAliasInput] = useState('');
  const [hasInitialized, setHasInitialized] = useState(false);
  const [discoveredUnit, setDiscoveredUnit] = useState<string | null>(null);
  const [isCreatingUnit, setIsCreatingUnit] = useState(false);

  const handleCreateDiscoveredUnit = async () => {
    if (!discoveredUnit) return;
    setIsCreatingUnit(true);
    try {
      const newUnit = await biomarkerService.createUnit({
        symbol: discoveredUnit,
        name: discoveredUnit,
        quantity_type: 'other'
      });
      setUnits(prev => [...prev, newUnit]);
      setFormData(prev => ({ ...prev, preferred_unit_id: newUnit.id }));
      setDiscoveredUnit(null);
    } catch (err) {
      console.error("Failed to create discovered unit", err);
    } finally {
      setIsCreatingUnit(false);
    }
  };

  useEffect(() => {
    if (isOpen && !hasInitialized) {
      biomarkerService.getUnits().then(setUnits);
      
      const slug = initialName.toLowerCase().replace(/[^a-z0-9]/g, '-');
      setFormData({
        name: initialName,
        slug,
        coding_system: 'loinc',
        code: '',
        category: '',
        aliases: [],
        preferred_unit_id: '',
        reference_range_min: '',
        reference_range_max: '',
        info: ''
      });
      
      setHasInitialized(true);
    }
  }, [isOpen, hasInitialized, initialName]);

  // Reset initialization when modal closes
  useEffect(() => {
    if (!isOpen) {
      setHasInitialized(false);
    }
  }, [isOpen]);

  const handleNameChange = (name: string) => {
    const slug = name.toLowerCase().replace(/[^a-z0-9]/g, '-');
    setFormData(prev => ({ ...prev, name, slug }));
  };

  const addAlias = () => {
    if (aliasInput.trim()) {
      const newAlias = aliasInput.trim();
      setFormData(prev => {
        if (prev.aliases.includes(newAlias)) return prev;
        return { 
          ...prev, 
          aliases: [...prev.aliases, newAlias] 
        };
      });
      setAliasInput('');
    }
  };

  const removeAlias = (alias: string) => {
    setFormData(prev => ({ 
      ...prev, 
      aliases: prev.aliases.filter(a => a !== alias) 
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name || !formData.slug) return;

    setLoading(true);
    try {
      const payload = {
        ...formData,
        reference_range_min: formData.reference_range_min === '' ? null : parseFloat(formData.reference_range_min),
        reference_range_max: formData.reference_range_max === '' ? null : parseFloat(formData.reference_range_max),
        preferred_unit_id: formData.preferred_unit_id || null
      };
      const result = await biomarkerService.createBiomarker(payload);
      onSuccess(result);
      onClose();
    } catch (err) {
      console.error("Failed to create biomarker", err);
      alert("Failed to create biomarker definition. Slug might already exist.");
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[1100] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
              <ListTree className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">New Biomarker Definition</h2>
              <p className="text-[10px] text-gray-400 font-black uppercase tracking-widest mt-0.5">Catalog Template</p>
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <AIAssistButton 
              taskType="define_biomarker" 
              context={{ initialName }} 
              placeholder="Enter biomarker (e.g. 'Creatinine definition')"
              onSuggestedData={(data) => {
                console.log("RECEIVED AI DATA:", data);
                
                // Find matching unit ID from the symbol
                let unitId = '';
                let unknownUnit = null;
                if (data.unit_symbol) {
                  const match = units.find(u => 
                    u.symbol.toLowerCase() === data.unit_symbol.toLowerCase() ||
                    u.name.toLowerCase() === data.unit_symbol.toLowerCase()
                  );
                  if (match) unitId = match.id;
                  else unknownUnit = data.unit_symbol;
                }
                
                setDiscoveredUnit(unknownUnit);

                setFormData(prev => {
                  const updated = { ...prev };
                  
                  if (data.name) {
                    updated.name = data.name;
                    updated.slug = data.name.toLowerCase().replace(/[^a-z0-9]/g, '-');
                  }
                  
                  if (data.category) updated.category = data.category;
                  if (unitId) updated.preferred_unit_id = unitId;
                  
                  if (data.reference_range_min !== undefined && data.reference_range_min !== null) {
                    updated.reference_range_min = data.reference_range_min.toString();
                  }
                  
                  if (data.reference_range_max !== undefined && data.reference_range_max !== null) {
                    updated.reference_range_max = data.reference_range_max.toString();
                  }
                  
                  if (data.coding_system) updated.coding_system = data.coding_system;
                  if (data.code) updated.code = data.code;
                  
                  if (data.info) updated.info = data.info;

                  if (data.aliases && Array.isArray(data.aliases)) {
                    updated.aliases = [...new Set([...prev.aliases, ...data.aliases])];
                  }

                  console.log("FINAL UPDATED STATE:", updated);
                  return updated;
                });
              }}
            />
            <button onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
              <X className="w-5 h-5 text-gray-400" />
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-8 space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">Display Name</label>
              <input
                type="text"
                placeholder="e.g. White Blood Cell Count"
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
                value={formData.name}
                onChange={e => handleNameChange(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">System Slug (Unique ID)</label>
              <input
                type="text"
                placeholder="e.g. wbc-count"
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-blue-600 dark:text-blue-400 font-mono text-sm focus:ring-2 focus:ring-blue-500/20"
                value={formData.slug}
                onChange={e => {
                  const val = e.target.value;
                  setFormData(prev => ({ ...prev, slug: val }));
                }}
                required
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">Coding System</label>
              <div className="relative">
                <select
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 appearance-none"
                  value={formData.coding_system}
                  onChange={e => setFormData(prev => ({ ...prev, coding_system: e.target.value }))}
                >
                  <option value="loinc">LOINC</option>
                  <option value="custom">Custom</option>
                </select>
                <div className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
                  <ChevronDown className="w-4 h-4 text-gray-400" />
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">{formData.coding_system === 'loinc' ? 'LOINC Code' : 'Custom Code'}</label>
              <input
                type="text"
                placeholder={formData.coding_system === 'loinc' ? 'e.g. 6690-2' : 'e.g. CUSTOM-WBC'}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
                value={formData.code}
                onChange={e => setFormData(prev => ({ ...prev, code: e.target.value }))}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">Category</label>
              <input
                type="text"
                placeholder="e.g. Hematology"
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
                value={formData.category}
                onChange={e => {
                  const val = e.target.value;
                  setFormData(prev => ({ ...prev, category: val }));
                }}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">Preferred Unit</label>
              
              {discoveredUnit && (
                <div className="mb-2 p-2 bg-indigo-50 dark:bg-indigo-900/10 border border-indigo-100 dark:border-indigo-900/30 rounded-xl flex items-center justify-between">
                   <div className="flex items-center gap-2">
                      <Sparkles className="w-3 h-3 text-indigo-500" />
                      <span className="text-[10px] font-bold text-indigo-700 dark:text-indigo-300">AI Suggested Unit: {discoveredUnit}</span>
                   </div>
                   <button
                    type="button"
                    disabled={isCreatingUnit}
                    onClick={handleCreateDiscoveredUnit}
                    className="px-2 py-1 bg-white dark:bg-dark-surface border border-indigo-200 dark:border-indigo-800 rounded-lg text-[9px] font-black uppercase text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 transition-all flex items-center gap-1"
                   >
                     {isCreatingUnit ? <Activity className="w-2.5 h-2.5 animate-spin" /> : <Plus className="w-2.5 h-2.5" />}
                     <span>Add Unit</span>
                   </button>
                </div>
              )}

              <UnitSelector
                units={units}
                selectedId={formData.preferred_unit_id}
                onSelect={(u) => setFormData(prev => ({ ...prev, preferred_unit_id: u.id }))}
                onUnitsUpdated={setUnits}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">Reference Range (Min)</label>
              <input
                type="number"
                step="any"
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
                value={formData.reference_range_min}
                onChange={e => {
                  const val = e.target.value;
                  setFormData(prev => ({ ...prev, reference_range_min: val }));
                }}
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">Reference Range (Max)</label>
              <input
                type="number"
                step="any"
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20"
                value={formData.reference_range_max}
                onChange={e => {
                  const val = e.target.value;
                  setFormData(prev => ({ ...prev, reference_range_max: val }));
                }}
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Tag className="w-3 h-3 mr-2" />
              Aliases & Synonyms
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Add synonym..."
                className="flex-1 px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-sm"
                value={aliasInput}
                onChange={e => setAliasInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addAlias())}
              />
              <button
                type="button"
                onClick={addAlias}
                className="px-4 bg-blue-50 dark:bg-blue-900/20 text-blue-600 rounded-xl hover:bg-blue-100"
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>
            <div className="flex flex-wrap gap-1 mt-2">
              {formData.aliases.map(a => (
                <span key={a} className="px-2 py-1 bg-gray-100 dark:bg-dark-bg rounded-lg text-[10px] font-bold flex items-center space-x-1">
                  <span>{a}</span>
                  <button type="button" onClick={() => removeAlias(a)} className="text-gray-400 hover:text-red-500">
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Info className="w-3 h-3 mr-2" />
              Standardized Info (Clinical Context)
            </label>
            <RichTextEditor 
              value={formData.info} 
              onChange={val => setFormData(prev => ({ ...prev, info: val }))} 
              placeholder="Clinical significance, normal ranges details..."
              minHeight="200px"
            />
          </div>
        </form>

        {/* Footer */}
        <div className="px-8 py-6 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center justify-end space-x-4">
          <button
            type="button"
            onClick={onClose}
            className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 transition-colors uppercase tracking-widest"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !formData.name || !formData.slug}
            className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2 uppercase tracking-widest"
          >
            {loading ? (
              <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>Create Definition</span>
          </button>
        </div>
      </div>
    </div>
  );
};
