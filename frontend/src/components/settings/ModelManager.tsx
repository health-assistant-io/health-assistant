import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { AIModel, AIProvider } from '../../api/aiConfig';
import { Settings, Cpu, X, Check, Trash2, Plus, Search, ChevronDown, Sparkles, Loader2, AlertCircle } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';

interface ModelManagerProps {
  provider: AIProvider;
}

export const ModelManager: React.FC<ModelManagerProps> = ({ provider }) => {
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const {
    models,
    createModel,
    updateModel,
    deleteModel,
    fetchExternalModels,
    error,
    clearError,
  } = useAIConfigStore();

  const [isCreating, setIsCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editData, setEditData] = useState<Partial<AIModel>>({});
  const [formData, setFormData] = useState({
    name: '',
    model_name: '',
    description: '',
    max_tokens: 65536,
    temperature: 0.7,
    is_active: true,
  });

  // External models state
  const [externalModels, setExternalModels] = useState<any[]>([]);
  const [isFetchingModels, setIsFetchingModels] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showDropdown, setShowDropdown] = useState(false);
  const [modelSearch, setModelSearch] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  const isOpenAI = provider.provider_type === 'openai';

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
        setModelSearch('');
      }
    };

    if (showDropdown) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showDropdown]);

  useEffect(() => {
    // Only fetch if we are creating or editing AND we haven't fetched yet
    if ((isCreating || editingId) && isOpenAI && externalModels.length === 0 && !isFetchingModels) {
      const loadExternal = async () => {
        setIsFetchingModels(true);
        setFetchError(null);
        try {
          const fetched = await fetchExternalModels(provider.id);
          setExternalModels(fetched);
        } catch (err: any) {
          console.error('Failed to fetch external models:', err);
          setFetchError(err.message || 'Failed to connect to provider API');
        } finally {
          setIsFetchingModels(false);
        }
      };
      loadExternal();
    }
  }, [isCreating, editingId, isOpenAI, provider.id, fetchExternalModels, externalModels.length, isFetchingModels]);

  const filteredExternalModels = useMemo(() => {
    const fuzzyMatch = (text: string, query: string) => {
      // 1. Normalize for basic comparisons
      const cleanText = text.toLowerCase();
      const cleanQuery = query.toLowerCase();
      
      if (!cleanQuery.trim()) return true;
      
      // 2. Direct includes check
      if (cleanText.includes(cleanQuery)) return true;
      
      // 3. Normalized includes (ignore spaces/dashes/dots)
      const superCleanText = cleanText.replace(/[^a-z0-9]/g, '');
      const superCleanQuery = cleanQuery.replace(/[^a-z0-9]/g, '');
      if (superCleanText.includes(superCleanQuery)) return true;
      
      // 4. Sequence matching (fuzzy)
      // Checks if characters in query appear in text in the same order
      let textIdx = 0;
      let queryIdx = 0;
      const queryChars = cleanQuery.replace(/\s+/g, ''); // ignore spaces in query
      
      while (textIdx < cleanText.length && queryIdx < queryChars.length) {
        if (cleanText[textIdx] === queryChars[queryIdx]) {
          queryIdx++;
        }
        textIdx++;
      }
      
      return queryIdx === queryChars.length;
    };

    if (!modelSearch) return externalModels;
    return externalModels.filter(m => fuzzyMatch(m.id, modelSearch));
  }, [externalModels, modelSearch]);

  const handleCreate = async () => {
    if (!formData.name || !formData.model_name) {
      alert('Please fill in both the display name and model identifier');
      return;
    }

    try {
      await createModel(provider.id, {
        ...formData,
        provider_id: provider.id,
      });
      setIsCreating(false);
      setFormData({
        name: '',
        model_name: '',
        description: '',
        max_tokens: 65536,
        temperature: 0.7,
        is_active: true,
      });
    } catch (err) {
      console.error('Failed to create model:', err);
    }
  };

  const handleUpdate = async (id: string, data: Partial<AIModel>) => {
    try {
      await updateModel(id, data);
      setEditingId(null);
      setEditData({});
    } catch (err) {
      console.error('Failed to update model:', err);
    }
  };

  const handleEditChange = (field: string, value: any) => {
    setEditData(prev => ({ ...prev, [field]: value }));
  };

  const handleSaveEdit = (id: string) => {
    handleUpdate(id, editData);
  };

  const handleDelete = async (id: string) => {
    showConfirmation({
      title: 'Delete Model',
      message: 'Are you sure you want to delete this model definition?',
      confirmLabel: 'Delete Model',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteModel(id);
        } catch (err) {
          console.error('Failed to delete model:', err);
        }
      }
    });
  };

  const selectExternalModel = (modelId: string, isForEdit: boolean) => {
    // Beautify modelId for display name
    // 1. Replace dashes and colons with spaces
    // 2. Replace dots with spaces UNLESS they are between digits (like 3.5)
    const beautifiedName = modelId
      .replace(/[-:]/g, ' ')
      .replace(/(?<!\d)\.|\.(?!\d)/g, ' ')
      .split(' ')
      .filter(Boolean)
      .map(word => {
        if (['gpt', 'nlp', 'ocr', 'llm'].includes(word.toLowerCase())) {
          return word.toUpperCase();
        }
        return word.charAt(0).toUpperCase() + word.slice(1);
      })
      .join(' ');

    if (isForEdit) {
      handleEditChange('model_name', modelId);
      // Auto-set display name if it's currently empty or was previously auto-filled (matches current model_name)
      const currentModel = models.find(m => m.id === editingId);
      if (!editData.name && (!currentModel?.name || currentModel.name === currentModel.model_name)) {
        handleEditChange('name', beautifiedName);
      }
    } else {
      setFormData(prev => ({
        ...prev,
        model_name: modelId,
        // Overwrite name if it's empty or looks like an ID
        name: (prev.name === '' || prev.name === prev.model_name) ? beautifiedName : prev.name
      }));
    }
    setShowDropdown(false);
    setModelSearch('');
  };

  const providerModels = models.filter(m => m.provider_id === provider.id);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">
          Models for {provider.name}
        </h3>
        {!isCreating && (
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold transition-all shadow-sm active:scale-95"
          >
            <Plus className="w-4 h-4" />
            <span>Add Model</span>
          </button>
        )}
      </div>

      {(error || fetchError) && (
        <div className="p-3 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg flex items-center justify-between border border-red-200 dark:border-red-900/50">
          <div className="flex items-center">
            <AlertCircle className="w-4 h-4 mr-2" />
            <span className="text-sm">{error || fetchError}</span>
          </div>
          <button onClick={() => { clearError(); setFetchError(null); }} className="text-xs underline font-bold px-2 py-1">Dismiss</button>
        </div>
      )}

      {/* Create Model Form */}
      {isCreating && (
        <div className="p-6 bg-white dark:bg-dark-surface rounded-xl border-2 border-blue-500 dark:border-blue-600 shadow-xl mb-6 animate-in slide-in-from-top-4 duration-300 relative overflow-visible z-10">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center space-x-2">
              <div className="p-2 bg-blue-100 dark:bg-blue-900/40 rounded-lg">
                <Sparkles className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <h4 className="text-md font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">
                Define New Model
              </h4>
            </div>
            <button onClick={() => setIsCreating(false)} className="p-2 text-gray-400 hover:text-red-500 transition-colors rounded-full hover:bg-gray-100 dark:hover:bg-dark-bg">
              <X className="w-5 h-5" />
            </button>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex items-center">
                Display Name <span className="text-red-500 ml-1">*</span>
              </label>
              <input
                type="text"
                autoFocus
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm shadow-inner outline-none focus:ring-2 focus:ring-blue-500/30 dark:text-dark-text transition-all"
                placeholder="e.g. GPT-4o (Clinical)"
              />
            </div>
            
            <div className="space-y-2 relative">
              <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex items-center">
                API Identifier <span className="text-red-500 ml-1">*</span>
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={formData.model_name}
                  onChange={(e) => setFormData({ ...formData, model_name: e.target.value })}
                  className={`w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm shadow-inner outline-none focus:ring-2 focus:ring-blue-500/30 dark:text-dark-text transition-all ${isOpenAI ? 'pr-12' : ''}`}
                  placeholder="e.g. gpt-4o"
                />
                {isOpenAI && (
                  <button 
                    type="button"
                    onClick={(e) => { e.stopPropagation(); setShowDropdown(!showDropdown); }}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-2 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition-all"
                    title="Browse official model list"
                  >
                    {isFetchingModels ? <Loader2 className="w-5 h-5 animate-spin" /> : <ChevronDown className={`w-5 h-5 transition-transform duration-300 ${showDropdown ? 'rotate-180' : ''}`} />}
                  </button>
                )}
              </div>
              
              {showDropdown && isOpenAI && (
                <div 
                  ref={dropdownRef}
                  className="absolute z-[100] w-full mt-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200 ring-4 ring-blue-500/10"
                >
                  <div className="p-3 border-b border-gray-100 dark:border-dark-border sticky top-0 bg-white/90 dark:bg-dark-surface/90 backdrop-blur-md z-10">
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                      <input
                        type="text"
                        autoFocus
                        placeholder="Search model catalog..."
                        className="w-full pl-10 pr-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                        value={modelSearch}
                        onChange={(e) => setModelSearch(e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </div>
                  </div>
                  <div className="max-h-64 overflow-y-auto custom-scrollbar p-1 relative z-0">
                    {filteredExternalModels.length > 0 ? (
                      filteredExternalModels.map((m) => (
                        <div
                          key={m.id}
                          className="px-4 py-3 text-sm hover:bg-blue-50 dark:hover:bg-blue-900/40 cursor-pointer flex items-center justify-between group/item transition-all rounded-lg m-1 pointer-events-auto"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            selectExternalModel(m.id, false);
                          }}
                        >
                          <div className="flex flex-col pointer-events-none">
                            <span className="font-bold text-gray-700 dark:text-dark-text group-hover:text-blue-700 dark:group-hover:text-blue-400 transition-colors">{m.id}</span>
                            <span className="text-[10px] text-gray-400 uppercase tracking-tighter">Owned by: {m.owned_by}</span>
                          </div>
                          <div className="p-1.5 rounded-full bg-transparent group-hover:bg-white dark:group-hover:bg-dark-surface shadow-none group-hover:shadow-sm transition-all pointer-events-none">
                            <Plus className="w-4 h-4 text-blue-500 opacity-0 group-hover:opacity-100 transition-opacity" />
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="px-4 py-8 text-sm text-gray-400 italic text-center flex flex-col items-center">
                        {isFetchingModels ? (
                          <>
                            <Loader2 className="w-8 h-8 animate-spin text-blue-400 mb-2 opacity-50" />
                            <span>Connecting to {provider.name}...</span>
                          </>
                        ) : (
                          <>
                            <Search className="w-8 h-8 text-gray-200 mb-2" />
                            <span>No models matching "{modelSearch}"</span>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="md:col-span-2 space-y-2">
              <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1">Short Description</label>
              <input
                type="text"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm shadow-inner outline-none focus:ring-2 focus:ring-blue-500/30 dark:text-dark-text transition-all"
                placeholder="What is this model used for?"
              />
            </div>
            
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex justify-between">
                <span>Max Tokens</span>
                <span className="text-blue-600 dark:text-blue-400">{formData.max_tokens.toLocaleString()}</span>
              </label>
              <input
                type="number"
                value={formData.max_tokens}
                onChange={(e) => setFormData({ ...formData, max_tokens: parseInt(e.target.value) || 0 })}
                className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm shadow-inner outline-none focus:ring-2 focus:ring-blue-500/30 dark:text-dark-text transition-all"
              />
            </div>
            
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex justify-between">
                <span>Creativity (Temperature)</span>
                <span className="text-blue-600 dark:text-blue-400">{formData.temperature}</span>
              </label>
              <div className="px-2 pt-2">
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={formData.temperature}
                  onChange={(e) => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                  className="w-full h-2 bg-gray-200 dark:bg-dark-border rounded-lg appearance-none cursor-pointer accent-blue-600"
                />
                <div className="flex justify-between text-[8px] text-gray-400 mt-2 font-black uppercase tracking-widest">
                  <span>Precise</span>
                  <span>Balanced</span>
                  <span>Creative</span>
                </div>
              </div>
            </div>
            
            <div className="flex items-center space-x-6 md:col-span-2 pt-2 ml-1">
              <label className="flex items-center cursor-pointer group">
                <input
                  type="checkbox"
                  checked={formData.is_active}
                  onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                  className="w-5 h-5 text-blue-600 border-gray-300 rounded-lg focus:ring-blue-500 dark:bg-dark-bg dark:border-dark-border transition-all"
                />
                <span className="ml-3 text-xs font-black text-gray-500 dark:text-dark-muted group-hover:text-gray-900 dark:group-hover:text-dark-text uppercase tracking-widest transition-colors">Enabled for System Use</span>
              </label>
            </div>
          </div>
          
          <div className="mt-8 pt-6 border-t border-gray-100 dark:border-dark-border flex justify-end items-center space-x-4">
            <button
              onClick={() => setIsCreating(false)}
              className="px-6 py-2.5 text-sm font-black text-gray-400 hover:text-gray-600 dark:hover:text-dark-text transition-colors uppercase tracking-widest"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              className="px-10 py-3 bg-blue-600 text-white rounded-2xl font-black text-sm hover:bg-blue-700 shadow-lg shadow-blue-200 dark:shadow-none transition-all active:scale-95 flex items-center space-x-2 uppercase tracking-widest"
            >
              <Check className="w-5 h-5" />
              <span>Save Model Definition</span>
            </button>
          </div>
        </div>
      )}

      {/* Model List */}
      <div className="space-y-3">
        {providerModels.length === 0 && !isCreating && (
          <div className="py-16 text-center bg-white dark:bg-dark-surface rounded-2xl border-2 border-dashed border-gray-200 dark:border-dark-border shadow-inner">
            <div className="w-20 h-20 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-6">
              <Cpu className="w-10 h-10 text-gray-300 dark:text-dark-muted" />
            </div>
            <h4 className="text-lg font-bold text-gray-900 dark:text-dark-text mb-2">No models configured</h4>
            <p className="text-sm text-gray-500 dark:text-dark-muted max-w-xs mx-auto mb-8">
              Define the AI models you want to use with {provider.name}. You can fetch them directly from the API.
            </p>
            <button 
              onClick={() => setIsCreating(true)}
              className="inline-flex items-center px-8 py-3 bg-blue-600 text-white rounded-2xl font-black text-sm hover:bg-blue-700 shadow-xl shadow-blue-100 dark:shadow-none transition-all active:scale-95 uppercase tracking-widest gap-2"
            >
              <Plus className="w-5 h-5" />
              Add First Model
            </button>
          </div>
        )}
        
        {providerModels.map((model) => {
          const isEditing = editingId === model.id;
          
          return (
            <div
              key={model.id}
              className={`p-4 transition-all rounded-xl border-2 ${isEditing ? 'border-blue-500 bg-white dark:bg-dark-surface shadow-2xl relative z-10' : 'border-gray-100 dark:border-dark-border bg-white dark:bg-dark-surface hover:border-blue-200 cursor-pointer group/model'}`}
              onClick={() => {
                if (!isEditing) {
                  setEditingId(model.id);
                  setEditData(model);
                }
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-4">
                  <div className={`p-2.5 rounded-xl transition-all ${isEditing ? 'bg-blue-600 text-white' : 'bg-gray-50 dark:bg-dark-bg text-gray-400 group-hover/model:text-blue-500 group-hover/model:bg-blue-50 dark:group-hover/model:bg-blue-900/20'}`}>
                    <Cpu className={`w-5 h-5 ${isEditing ? 'animate-pulse' : ''}`} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-0.5">
                      <h4 className="text-sm font-bold text-gray-900 dark:text-dark-text leading-tight">
                        {model.name}
                      </h4>
                      {!model.is_active && (
                        <span className="px-2 py-0.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted text-[8px] font-black uppercase tracking-tighter rounded border border-gray-200 dark:border-dark-border">
                          Disabled
                        </span>
                      )}
                    </div>
                    <p className="text-[10px] font-mono text-gray-400 flex items-center">
                      <span className="bg-gray-100 dark:bg-dark-bg px-1.5 py-0.5 rounded mr-2 opacity-80">{model.model_name}</span>
                      <span className="w-1 h-1 bg-gray-300 rounded-full mx-2" />
                      <span className="font-medium">{model.max_tokens?.toLocaleString() || '65,536'} context</span>
                      <span className="w-1 h-1 bg-gray-300 rounded-full mx-2" />
                      <span className="font-medium">Temp: {model.temperature}</span>
                    </p>
                  </div>
                </div>

                {!isEditing && (
                  <div className="flex items-center space-x-2 opacity-0 group-hover/model:opacity-100 transition-all duration-300">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(model.id);
                      }}
                      className="p-2 text-gray-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-all"
                      title="Delete Model"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                    <div className="px-3 py-1 bg-gray-50 dark:bg-dark-bg text-[10px] font-black uppercase tracking-widest text-gray-400 rounded-lg border border-gray-100 dark:border-dark-border group-hover/model:border-blue-200 group-hover/model:text-blue-500 transition-all">
                      Configure
                    </div>
                  </div>
                )}
              </div>

              {isEditing && (
                <div className="mt-6 p-6 bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl border border-gray-100 dark:border-dark-border space-y-6 animate-in slide-in-from-top-2 duration-300" onClick={e => e.stopPropagation()}>
                  <h5 className="text-[10px] font-black uppercase text-blue-600 dark:text-blue-400 tracking-[0.2em] flex items-center">
                    <Settings className="w-3.5 h-3.5 mr-2 animate-spin-slow" />
                    Update model configuration
                  </h5>
                  
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="space-y-1">
                      <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1">Display Name</label>
                      <input
                        type="text"
                        value={editData.name ?? model.name}
                        onChange={(e) => handleEditChange('name', e.target.value)}
                        className="w-full px-4 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm text-gray-900 dark:text-dark-text outline-none focus:ring-2 focus:ring-blue-500/20 transition-all shadow-sm"
                      />
                    </div>
                    
                    <div className="space-y-1 relative">
                      <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1">API Identifier</label>
                      <div className="relative">
                        <input
                          type="text"
                          value={editData.model_name ?? model.model_name}
                          onChange={(e) => handleEditChange('model_name', e.target.value)}
                          className={`w-full px-4 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm text-gray-900 dark:text-dark-text outline-none focus:ring-2 focus:ring-blue-500/20 transition-all shadow-sm ${isOpenAI ? 'pr-12' : ''}`}
                        />
                        {isOpenAI && (
                          <button 
                            type="button"
                            onClick={(e) => { e.stopPropagation(); setShowDropdown(!showDropdown); }}
                            className="absolute right-2 top-1/2 -translate-y-1/2 p-2 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition-all"
                          >
                            {isFetchingModels ? <Loader2 className="w-5 h-5 animate-spin" /> : <ChevronDown className={`w-5 h-5 transition-transform duration-300 ${showDropdown ? 'rotate-180' : ''}`} />}
                          </button>
                        )}
                      </div>
                      
                      {showDropdown && isOpenAI && (
                        <div 
                          ref={dropdownRef}
                          className="absolute z-[100] w-full mt-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200 ring-4 ring-blue-500/10"
                        >
                          <div className="p-3 border-b border-gray-100 dark:border-dark-border sticky top-0 bg-white/90 dark:bg-dark-surface/90 backdrop-blur-md z-10">
                            <div className="relative">
                              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                              <input
                                type="text"
                                autoFocus
                                placeholder="Search models..."
                                className="w-full pl-10 pr-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                                value={modelSearch}
                                onChange={(e) => setModelSearch(e.target.value)}
                                onClick={(e) => e.stopPropagation()}
                              />
                            </div>
                          </div>
                          <div className="max-h-64 overflow-y-auto custom-scrollbar p-1 relative z-0">
                            {filteredExternalModels.length > 0 ? (
                              filteredExternalModels.map((m) => (
                                <div
                                  key={m.id}
                                  className="px-4 py-3 text-sm hover:bg-blue-50 dark:hover:bg-blue-900/40 cursor-pointer flex items-center justify-between group/item transition-all rounded-lg m-1 pointer-events-auto"
                                  onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    selectExternalModel(m.id, true);
                                  }}
                                >
                                  <div className="flex flex-col pointer-events-none">
                                    <span className="font-bold text-gray-700 dark:text-dark-text">{m.id}</span>
                                    <span className="text-[10px] text-gray-400">Official ID</span>
                                  </div>
                                  <Check className={`w-4 h-4 text-blue-500 transition-opacity pointer-events-none ${editData.model_name === m.id ? 'opacity-100' : 'opacity-0'}`} />
                                </div>
                              ))
                            ) : (
                              <div className="px-4 py-8 text-xs text-gray-400 text-center italic">
                                {isFetchingModels ? 'Connecting to catalog...' : 'No results'}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="space-y-1">
                      <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1">Context Limit</label>
                      <input
                        type="number"
                        value={editData.max_tokens ?? model.max_tokens}
                        onChange={(e) => handleEditChange('max_tokens', parseInt(e.target.value) || 0)}
                        className="w-full px-4 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm text-gray-900 dark:text-dark-text outline-none focus:ring-2 focus:ring-blue-500/20 transition-all shadow-sm"
                      />
                    </div>
                    
                    <div className="space-y-1">
                      <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex justify-between">
                        <span>Temperature</span>
                        <span className="text-blue-600 dark:text-blue-400">{editData.temperature ?? model.temperature}</span>
                      </label>
                      <input
                        type="range"
                        min={0} max={2} step={0.1}
                        value={editData.temperature !== undefined ? editData.temperature : model.temperature}
                        onChange={(e) => handleEditChange('temperature', parseFloat(e.target.value))}
                        className="w-full h-1.5 bg-gray-200 dark:bg-dark-border rounded-lg appearance-none cursor-pointer accent-blue-600 mt-3"
                      />
                    </div>
                  </div>
                  
                  <div className="flex items-center justify-between pt-6 border-t border-gray-100 dark:border-dark-border">
                    <div className="flex items-center space-x-6 ml-1">
                      <label className="flex items-center cursor-pointer group/toggle">
                        <input
                          type="checkbox"
                          checked={editData.is_active !== undefined ? editData.is_active : model.is_active}
                          onChange={(e) => handleEditChange('is_active', e.target.checked)}
                          className="w-5 h-5 text-blue-600 border-gray-300 dark:border-dark-border rounded-lg bg-white dark:bg-dark-bg focus:ring-blue-500 transition-all"
                        />
                        <span className="ml-3 text-[10px] font-black text-gray-400 dark:text-dark-muted group-hover/toggle:text-gray-700 dark:group-hover/toggle:text-dark-text uppercase tracking-widest transition-colors">Enabled</span>
                      </label>
                    </div>
                    
                    <div className="flex items-center space-x-3">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(model.id);
                        }}
                        className="p-2.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all mr-2"
                        title="Delete Model"
                      >
                        <Trash2 className="w-5 h-5" />
                      </button>
                      <button
                        onClick={() => {
                          setEditingId(null);
                          setEditData({});
                          setShowDropdown(false);
                        }}
                        className="px-6 py-2.5 text-sm font-black text-gray-400 hover:text-gray-600 dark:hover:text-dark-text transition-colors uppercase tracking-widest"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => handleSaveEdit(model.id)}
                        className="px-10 py-3 bg-blue-600 text-white rounded-2xl font-black text-sm hover:bg-blue-700 shadow-xl shadow-blue-100 dark:shadow-none transition-all active:scale-95 flex items-center space-x-2 uppercase tracking-widest"
                      >
                        <Check className="w-5 h-5" />
                        <span>Save Changes</span>
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      
      {/* No global overlay, we use dropdownRef and mousedown listener instead */}
    </div>
  );
};
