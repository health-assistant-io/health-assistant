import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { AIModel, AIProvider, AIModelCapability, ALL_MODEL_CAPABILITIES } from '../../api/aiConfig';
import { Settings, Cpu, X, Check, Trash2, Plus, Search, ChevronDown, Sparkles, Loader2, AlertCircle, Eye, AudioLines, FileText } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';
import { ProviderForm, ModelPicker } from '@neuronection/assistant-ui';

interface ModelManagerProps {
  provider: AIProvider;
}

/** Display metadata for each capability (icon + label + tone). */
const CAPABILITY_META: Record<AIModelCapability, { icon: typeof Eye; label: string; tone: string }> = {
  text: { icon: FileText, label: 'Text', tone: 'bg-blue-50 dark:bg-blue-900/30 text-blue-600' },
  vision: { icon: Eye, label: 'Vision', tone: 'bg-purple-50 dark:bg-purple-900/30 text-purple-600' },
  audio_input: { icon: AudioLines, label: 'Audio Input', tone: 'bg-amber-50 dark:bg-amber-900/30 text-amber-600' },
};

/** Toggle chip group for a model's capabilities. ``text`` is the default
 *  modality (selected for new models) but is NOT locked — an STT-only model
 *  like ``whisper-1`` legitimately carries only ``audio_input``. The only
 *  constraint: at least one capability must remain (you can't empty the set). */
const CapabilityToggles: React.FC<{
  value: AIModelCapability[];
  onChange: (next: AIModelCapability[]) => void;
}> = ({ value, onChange }) => {
  const set = new Set(value);
  const toggle = (cap: AIModelCapability) => {
    if (set.has(cap)) {
      // Removing — refuse to remove the last remaining capability.
      if (value.length <= 1) return;
      onChange(value.filter((c) => c !== cap));
    } else {
      onChange([...value, cap]);
    }
  };
  return (
    <div className="flex flex-wrap gap-2">
      {ALL_MODEL_CAPABILITIES.map((cap) => {
        const meta = CAPABILITY_META[cap];
        const active = set.has(cap);
        const Icon = meta.icon;
        const isLastActive = active && value.length <= 1;
        return (
          <button
            key={cap}
            type="button"
            onClick={() => toggle(cap)}
            disabled={isLastActive}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-xl border text-xs font-bold uppercase tracking-wider transition-all ${
              active
                ? `${meta.tone} border-current/20 shadow-sm`
                : 'bg-gray-50 dark:bg-dark-bg text-gray-400 dark:text-dark-muted border-gray-200 dark:border-dark-border hover:border-gray-300'
            } ${isLastActive ? 'cursor-not-allowed opacity-90' : 'cursor-pointer hover:scale-[1.03]'}`}
            title={
              isLastActive
                ? 'At least one capability is required'
                : `${meta.label} capability`
            }
          >
            <Icon className="w-3.5 h-3.5" />
            {meta.label}
            {active && <Check className="w-3 h-3" />}
          </button>
        );
      })}
    </div>
  );
};

/** Compact capability badges for the model list row. */
const CapabilityBadges: React.FC<{ capabilities?: AIModelCapability[] }> = ({ capabilities }) => {
  const caps = (capabilities && capabilities.length ? capabilities : ['text']) as AIModelCapability[];
  return (
    <>
      {caps.map((cap) => {
        const meta = CAPABILITY_META[cap];
        if (!meta) return null;
        const Icon = meta.icon;
        return (
          <span
            key={cap}
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[8px] font-black uppercase ${meta.tone}`}
            title={`${meta.label} capability`}
          >
            <Icon className="w-2.5 h-2.5" />
            {meta.label}
          </span>
        );
      })}
    </>
  );
};

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
    capabilities: ['text'] as AIModelCapability[],
    max_tokens: 65536,
    temperature: 0.7,
    is_active: true,
    is_local: false,
  });

  // External models state
  const [externalModels, setExternalModels] = useState<any[]>([]);
  const [isFetchingModels, setIsFetchingModels] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const isOpenAI = provider.provider_type === 'openai';

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
        capabilities: ['text'],
        max_tokens: 65536,
        temperature: 0.7,
        is_active: true,
        is_local: false,
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
              </div>
              {isOpenAI && (
                <ModelPicker
                  providers={[
                    {
                      id: provider.id,
                      name: provider.name,
                      models: externalModels.map((m) => ({ id: m.id, name: m.id, capability: m.owned_by })),
                    },
                  ]}
                  value={formData.model_name}
                  onChange={(modelId: string) => selectExternalModel(modelId, false)}
                  loading={isFetchingModels}
                  label="Browse official model list"
                  searchPlaceholder="Search model catalog..."
                />
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

            <div className="md:col-span-2 space-y-2">
              <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex items-center justify-between">
                <span>Model Features / Capabilities</span>
                <span className="text-gray-300 normal-case font-medium tracking-normal">Select what this model supports</span>
              </label>
              <CapabilityToggles
                value={formData.capabilities}
                onChange={(next) => setFormData({ ...formData, capabilities: next })}
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
            
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex items-center">
                Deployment Type
              </label>
              <select
                value={formData.is_local ? 'local' : 'cloud'}
                onChange={(e) => setFormData({ ...formData, is_local: e.target.value === 'local' })}
                className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm shadow-inner outline-none focus:ring-2 focus:ring-blue-500/30 dark:text-dark-text transition-all"
              >
                <option value="cloud">☁️ Cloud Override</option>
                <option value="local">🏠 Local Override</option>
              </select>
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
                    <p className="text-[10px] font-mono text-gray-400 flex items-center flex-wrap gap-y-1">
                      <span className="bg-gray-100 dark:bg-dark-bg px-1.5 py-0.5 rounded mr-2 opacity-80">{model.model_name}</span>
                      <span className="w-1 h-1 bg-gray-300 rounded-full mx-2" />
                      <span className="font-medium">{model.max_tokens?.toLocaleString() || '65,536'} context</span>
                      <span className="w-1 h-1 bg-gray-300 rounded-full mx-2" />
                      <span className="font-medium">Temp: {model.temperature}</span>
                      <span className="w-1 h-1 bg-gray-300 rounded-full mx-2" />
                      <span className="flex items-center gap-1"><CapabilityBadges capabilities={model.capabilities} /></span>
                      <span className="w-1 h-1 bg-gray-300 rounded-full mx-2" />
                      {model.is_local ? (
                        <span className="px-1.5 py-0.5 bg-green-50 dark:bg-green-900/30 text-green-600 text-[8px] font-black uppercase rounded">Local Override</span>
                      ) : (
                        <span className="px-1.5 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-blue-600 text-[8px] font-black uppercase rounded">Cloud Override</span>
                      )}
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
                        value={editData.name ?? model.name ?? ''}
                        onChange={(e) => handleEditChange('name', e.target.value)}
                        className="w-full px-4 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm text-gray-900 dark:text-dark-text outline-none focus:ring-2 focus:ring-blue-500/20 transition-all shadow-sm"
                      />
                    </div>
                    
                    <div className="space-y-1 relative">
                      <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1">API Identifier</label>
                      <div className="relative">
                        <input
                          type="text"
                          value={editData.model_name ?? model.model_name ?? ''}
                          onChange={(e) => handleEditChange('model_name', e.target.value)}
                          className={`w-full px-4 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm text-gray-900 dark:text-dark-text outline-none focus:ring-2 focus:ring-blue-500/20 transition-all shadow-sm \${isOpenAI ? 'pr-12' : ''}`}
                        />
                        {isOpenAI && (
                          <ModelPicker
                            providers={[
                              {
                                id: provider.id,
                                name: provider.name,
                                models: externalModels.map((m) => ({ id: m.id, name: m.id, capability: m.owned_by })),
                              },
                            ]}
                            value={editData.model_name ?? model.model_name ?? ''}
                            onChange={(modelId: string) => selectExternalModel(modelId, true)}
                            loading={isFetchingModels}
                            label="Browse official model list"
                            searchPlaceholder="Search models..."
                          />
                        )}
                      </div>
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

                    <div className="space-y-1">
                      <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1 flex justify-between">
                        <span>Deployment Override</span>
                      </label>
                      <select
                        value={(editData.is_local !== undefined ? editData.is_local : model.is_local) ? 'local' : 'cloud'}
                        onChange={(e) => handleEditChange('is_local', e.target.value === 'local')}
                        className="w-full px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500/20 transition-all shadow-sm dark:text-dark-text"
                      >
                        <option value="cloud">☁️ Cloud</option>
                        <option value="local">🏠 Local</option>
                      </select>
                    </div>

                    <div className="md:col-span-2 space-y-1">
                      <label className="text-[10px] font-black uppercase text-gray-400 dark:text-dark-muted tracking-widest ml-1">Model Features / Capabilities</label>
                      <CapabilityToggles
                        value={(editData.capabilities as AIModelCapability[]) ?? model.capabilities ?? ['text']}
                        onChange={(next) => handleEditChange('capabilities', next)}
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
