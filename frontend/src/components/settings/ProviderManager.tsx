import React, { useState } from 'react';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { AIProvider } from '../../api/aiConfig';
import { Database, Settings, Trash2, Plus, X, Check, Search, Globe, Shield, User } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';

interface ProviderManagerProps {
  onProviderSelected?: (provider: AIProvider) => void;
  selectedProviderId?: string;
  scope?: 'global' | 'tenant' | 'user';
  userId?: string;
  tenantId?: string;
}

export const ProviderManager: React.FC<ProviderManagerProps> = ({ 
  onProviderSelected,
  selectedProviderId,
  scope = 'user',
  userId,
  tenantId
}) => {
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const {
    providers,
    createProvider,
    updateProvider,
    deleteProvider,
    error,
    clearError,
  } = useAIConfigStore();

  const [isCreating, setIsCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editData, setEditData] = useState<Partial<AIProvider>>({});
  const [formData, setFormData] = useState({
    name: '',
    provider_type: 'openai',
    api_base: 'https://api.openai.com/v1',
    api_key: '',
    is_active: true,
  });

  const handleCreate = async () => {
    try {
      const apiScope = scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER';
      await createProvider({
        ...formData,
        scope: apiScope,
        user_id: scope === 'user' ? userId : undefined,
        tenant_id: scope === 'tenant' ? tenantId : undefined
      });
      setIsCreating(false);
      setFormData({
        name: '',
        provider_type: 'openai',
        api_base: 'https://api.openai.com/v1',
        api_key: '',
        is_active: true,
      });
    } catch (err) {
      console.error('Failed to create provider:', err);
    }
  };

  const handleUpdate = async (id: string, data: Partial<AIProvider>) => {
    try {
      await updateProvider(id, data);
      setEditingId(null);
      setEditData({});
    } catch (err) {
      console.error('Failed to update provider:', err);
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
      title: 'Delete Provider',
      message: 'Are you sure you want to delete this provider? This will also remove all its models.',
      confirmLabel: 'Delete Provider',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteProvider(id);
        } catch (err) {
          console.error('Failed to delete provider:', err);
        }
      }
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">
          AI Providers
        </h3>
        <button
          onClick={() => setIsCreating(true)}
          className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-bold transition-all shadow-sm active:scale-95"
        >
          <Plus className="w-4 h-4" />
          <span>Add Provider</span>
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-lg flex items-center justify-between">
          <span>{error}</span>
          <button onClick={clearError} className="text-sm underline font-bold">Dismiss</button>
        </div>
      )}

      {isCreating && (
        <div className="p-5 bg-blue-50/30 dark:bg-blue-900/5 rounded-xl border border-blue-100 dark:border-blue-900/30 mb-6 animate-in zoom-in-95 duration-200">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-bold text-blue-900 dark:text-blue-400 flex items-center">
              <Plus className="w-4 h-4 mr-2" />
              New Connection Profile
            </h4>
            <button onClick={() => setIsCreating(false)} className="text-gray-400 hover:text-red-500 transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="text-[10px] font-black uppercase text-gray-400 tracking-widest ml-1">Friendly Name</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm shadow-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                placeholder="e.g. Production OpenAI"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-black uppercase text-gray-400 tracking-widest ml-1">Type</label>
              <select
                value={formData.provider_type}
                onChange={(e) => setFormData({ ...formData, provider_type: e.target.value })}
                className="w-full px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm shadow-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
              >
                <option value="openai">OpenAI (LLM)</option>
                <option value="tesseract">Tesseract (OCR)</option>
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-black uppercase text-gray-400 tracking-widest ml-1">API Base URL</label>
              <input
                type="text"
                value={formData.api_base}
                onChange={(e) => setFormData({ ...formData, api_base: e.target.value })}
                className="w-full px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm shadow-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                placeholder="https://api.openai.com/v1"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-black uppercase text-gray-400 tracking-widest ml-1">API Key (Sensitive)</label>
              <input
                type="password"
                value={formData.api_key}
                onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
                className="w-full px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm shadow-sm outline-none focus:ring-2 focus:ring-blue-500/20 dark:text-dark-text"
                placeholder="sk-..."
              />
            </div>
            <div className="flex items-center space-x-6 md:col-span-2 pt-2 ml-1">
              <label className="flex items-center cursor-pointer group">
                <input
                  type="checkbox"
                  checked={formData.is_active}
                  onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                  className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                />
                <span className="ml-2 text-xs font-bold text-gray-500 dark:text-dark-muted group-hover:text-gray-700 dark:group-hover:text-dark-text uppercase tracking-tight">Active</span>
              </label>
            </div>
          </div>
          <div className="mt-6 flex justify-end space-x-3">
            <button
              onClick={() => setIsCreating(false)}
              className="px-6 py-2 text-sm font-bold text-gray-500 hover:text-gray-700 dark:hover:text-dark-text transition-colors"
            >
              Discard
            </button>
            <button
              onClick={handleCreate}
              className="px-8 py-2 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 shadow-md shadow-blue-100 transition-all active:scale-95"
            >
              Create Provider
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        {providers.length === 0 && !isCreating && (
          <p className="text-gray-500 dark:text-gray-400 text-center py-4">
            No providers configured. Click "Add Provider" to create one.
          </p>
        )}
        
        {providers.map((provider) => {
          const isSelected = onProviderSelected && selectedProviderId === provider.id;
          const isEditing = editingId === provider.id;

          const canConfigure = (provider.user_id === userId) || 
                              (scope === 'global' && !provider.user_id && !provider.tenant_id) ||
                              (scope === 'tenant' && provider.tenant_id === tenantId && !provider.user_id);

          return (
            <div
              key={provider.id}
              className={`p-4 transition-all rounded-xl border group/provider ${
                isEditing 
                  ? 'border-blue-500 bg-white dark:bg-dark-surface shadow-md' 
                  : isSelected
                    ? 'border-blue-300 bg-blue-50/20 dark:bg-blue-900/5 ring-1 ring-blue-100 dark:ring-blue-900/20'
                    : 'border-gray-100 dark:border-dark-border bg-white dark:bg-dark-surface hover:border-blue-200 cursor-pointer'
              }`}
              onClick={() => {
                if (!isEditing && canConfigure) {
                  if (onProviderSelected) onProviderSelected(provider);
                  setEditingId(provider.id);
                  setEditData(provider);
                }
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-4">
                  <div className={`p-2 rounded-lg transition-colors ${isEditing ? 'bg-blue-600 text-white' : isSelected ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600' : 'bg-gray-100 dark:bg-dark-bg text-gray-400 group-hover/provider:text-blue-500'}`}>
                    <Database className={`w-5 h-5 ${isEditing ? 'animate-spin-slow' : ''}`} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-0.5">
                      <h4 className="text-md font-bold text-gray-900 dark:text-dark-text">
                        {provider.name}
                      </h4>
                      {/* Scope Badge */}
                      {!provider.user_id && !provider.tenant_id && (
                        <span className="flex items-center gap-1 px-1.5 py-0.5 bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 text-[8px] font-black uppercase tracking-tighter rounded">
                          <Globe className="w-2 h-2" /> System
                        </span>
                      )}
                      {provider.tenant_id && !provider.user_id && (
                        <span className="flex items-center gap-1 px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 text-[8px] font-black uppercase tracking-tighter rounded">
                          <Shield className="w-2 h-2" /> Org
                        </span>
                      )}
                      {provider.user_id && (
                        <span className="flex items-center gap-1 px-1.5 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400 text-[8px] font-black uppercase tracking-tighter rounded">
                          <User className="w-2 h-2" /> Personal
                        </span>
                      )}
                      
                      {!provider.is_active && (
                        <span className="px-2 py-0.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted text-[8px] font-black uppercase tracking-tighter rounded">
                          Disabled
                        </span>
                      )}
                    </div>
                    <p className="text-[10px] font-medium text-gray-400 uppercase tracking-tight flex items-center">
                      <span className="mr-2">{provider.provider_type}</span>
                      <span className="w-1 h-1 bg-gray-300 rounded-full mr-2"></span>
                      <span className="font-mono lowercase opacity-70 truncate max-w-[200px]">{provider.api_base}</span>
                    </p>
                  </div>
                </div>

                {!isEditing && (
                  <div className="flex items-center space-x-2 opacity-0 group-hover/provider:opacity-100 transition-all duration-300">
                    {canConfigure && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(provider.id);
                        }}
                        className="p-1.5 text-gray-300 hover:text-red-400 transition-colors"
                        title="Delete Provider"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                    <div className="px-2 py-0.5 bg-gray-50 dark:bg-dark-bg text-[9px] font-bold uppercase tracking-tight text-gray-400 rounded border border-gray-100 dark:border-dark-border">
                      { canConfigure ? 'Configure' : 'View Only' }
                    </div>
                  </div>
                )}
                
                {isEditing && (
                  <Settings className="w-4 h-4 text-blue-500/50 animate-spin-slow" />
                )}
              </div>

              {/* Edit Provider Form Panel */}
              {isEditing && (
                <div 
                  className="mt-5 pt-4 border-t border-gray-100 dark:border-dark-border space-y-4"
                  onClick={e => e.stopPropagation()}
                >
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1">
                      <label className="text-[9px] font-black uppercase text-gray-400 tracking-widest ml-1">Friendly Name</label>
                      <input
                        type="text"
                        value={editData.name !== undefined ? editData.name : provider.name}
                        onChange={(e) => handleEditChange('name', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/10 dark:text-dark-text"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-[9px] font-black uppercase text-gray-400 tracking-widest ml-1">Base URL</label>
                      <input
                        type="text"
                        value={editData.api_base !== undefined ? editData.api_base : provider.api_base}
                        onChange={(e) => handleEditChange('api_base', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/10 dark:text-dark-text"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-[9px] font-black uppercase text-gray-400 tracking-widest ml-1">Secret API Key</label>
                      <input
                        type="password"
                        value={editData.api_key !== undefined ? editData.api_key : (provider.api_key || '')}
                        onChange={(e) => handleEditChange('api_key', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/10 dark:text-dark-text"
                        placeholder="••••••••••••"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-[9px] font-black uppercase text-gray-400 tracking-widest ml-1">Protocol Type</label>
                      <select
                        value={editData.provider_type !== undefined ? editData.provider_type : provider.provider_type}
                        onChange={(e) => handleEditChange('provider_type', e.target.value)}
                        className="w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/10 dark:text-dark-text"
                      >
                        <option value="openai">OpenAI</option>
                        <option value="tesseract">Tesseract</option>
                      </select>
                    </div>
                  </div>
                  
                  <div className="flex items-center justify-between pt-2 border-t border-gray-50 dark:border-dark-border">
                    <div className="flex items-center space-x-4 ml-1">
                      <label className="flex items-center cursor-pointer">
                        <input
                          type="checkbox"
                          checked={editData.is_active !== undefined ? editData.is_active : provider.is_active}
                          onChange={(e) => handleEditChange('is_active', e.target.checked)}
                          className="w-4 h-4 text-blue-600 border-gray-300 rounded"
                        />
                        <span className="ml-2 text-[10px] font-bold text-gray-600 uppercase">Active</span>
                      </label>
                    </div>
                    
                    <div className="flex items-center space-x-2">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(provider.id);
                        }}
                        className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors mr-2"
                        title="Delete Provider"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => { setEditingId(null); setEditData({}); }}
                        className="px-4 py-1.5 text-xs font-bold text-gray-400 hover:text-gray-600 transition-colors"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => handleSaveEdit(provider.id)}
                        className="px-6 py-1.5 bg-blue-600 text-white rounded-lg font-bold text-xs hover:bg-blue-700 shadow-md shadow-blue-100 transition-all active:scale-95 flex items-center space-x-1.5"
                      >
                        <Check className="w-3.5 h-3.5" />
                        <span>Apply Updates</span>
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
