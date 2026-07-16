import React, { useState, useMemo } from 'react';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { AITaskAssignment, AIProvider, AIModel } from '../../api/aiConfig';
import { Search, ChevronDown, Check, X, Cpu, Settings as SettingsIcon, Trash2, Globe, Shield, User, AudioLines, type LucideIcon } from 'lucide-react';
import { useUIStore } from '../../store/slices/uiSlice';

interface TaskTypeDef {
  value: string;
  label: string;
  /** Lucide icon for this task (defaults to Cpu). */
  icon: LucideIcon;
}

const TASK_TYPES: TaskTypeDef[] = [
  { value: 'default', label: 'Global Default Fallback', icon: Cpu },
  { value: 'ocr', label: 'Document Parsing (OCR)', icon: Cpu },
  { value: 'nlp', label: 'Text Analysis & Extraction (NLP)', icon: Cpu },
  { value: 'medication_interaction', label: 'Medication Interaction Check', icon: Cpu },
  { value: 'anomaly_detection', label: 'Anomaly Detection', icon: Cpu },
  { value: 'fill_biomarker_form', label: 'Biomarker Form Auto-Fill', icon: Cpu },
  { value: 'fill_medication_form', label: 'Medication Form Auto-Fill', icon: Cpu },
  { value: 'magic_fill_examination', label: 'Magic Fill Examination', icon: Cpu },
  { value: 'define_biomarker', label: 'Define New Biomarker', icon: Cpu },
  { value: 'define_medication', label: 'Define New Medication', icon: Cpu },
  { value: 'suggest_category_icon', label: 'Suggest Category Icon', icon: Cpu },
  { value: 'generate_category_icon', label: 'Generate Custom SVG Icon', icon: Cpu },
  { value: 'chat', label: 'Assistant Chat', icon: Cpu },
  { value: 'transcription', label: 'Voice Input (Speech-to-Text)', icon: AudioLines },
];

interface TaskAssignmentProps {
  scope?: 'global' | 'tenant' | 'user';
  userId?: string;
  tenantId?: string;
}

export const TaskAssignment: React.FC<TaskAssignmentProps> = ({ 
  scope = 'user', 
  userId,
  tenantId
}) => {
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const {
    providers,
    models,
    taskAssignments,
    createTaskAssignment,
    updateTaskAssignment,
    deleteTaskAssignment,
    error,
    clearError,
  } = useAIConfigStore();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editData, setEditData] = useState<Partial<AITaskAssignment>>({});
  
  // Search and Dropdown state
  const [isDropdownOpen, setIsDropdownOpen] = useState<string | null>(null); // assignmentId or 'new'
  const [searchTerm, setSearchTerm] = useState('');

  // Group models by provider for the dropdown
  const groupedModels = useMemo(() => {
    const groups: Record<string, { provider: AIProvider, models: AIModel[] }> = {};
    
    providers.forEach(p => {
      groups[p.id] = {
        provider: p,
        models: models.filter(m => m.provider_id === p.id)
      };
    });
    
    return Object.values(groups).filter(g => g.models.length > 0);
  }, [providers, models]);

  const filteredGroupedModels = useMemo(() => {
    const fuzzyMatch = (text: string, query: string) => {
      const cleanText = text.toLowerCase();
      const cleanQuery = query.toLowerCase();
      if (!cleanQuery.trim()) return true;
      if (cleanText.includes(cleanQuery)) return true;
      const superCleanText = cleanText.replace(/[^a-z0-9]/g, '');
      const superCleanQuery = cleanQuery.replace(/[^a-z0-9]/g, '');
      if (superCleanText.includes(superCleanQuery)) return true;
      let textIdx = 0;
      let queryIdx = 0;
      const queryChars = cleanQuery.replace(/\s+/g, '');
      while (textIdx < cleanText.length && queryIdx < queryChars.length) {
        if (cleanText[textIdx] === queryChars[queryIdx]) queryIdx++;
        textIdx++;
      }
      return queryIdx === queryChars.length;
    };

    if (!searchTerm) return groupedModels;
    
    return groupedModels.map(group => ({
      ...group,
      models: group.models.filter(m => 
        fuzzyMatch(m.name, searchTerm) || 
        fuzzyMatch(group.provider.name, searchTerm) ||
        fuzzyMatch(m.model_name, searchTerm)
      )
    })).filter(group => group.models.length > 0);
  }, [groupedModels, searchTerm]);

  const getAssignmentForTaskType = (taskType: string) => {
    return taskAssignments.find(a => a.task_type === taskType && a.is_active);
  };

  const handleCreateAssignment = async (taskType: string, providerId?: string, modelId?: string) => {
    try {
      const apiScope = scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER';
      await createTaskAssignment({
        task_type: taskType,
        scope: apiScope,
        provider_id: providerId,
        model_id: modelId,
        is_active: true,
        priority: 0,
        user_id: scope === 'user' ? userId : undefined,
        tenant_id: scope === 'tenant' ? tenantId : undefined,
      });
    } catch (err) {
      console.error('Failed to create assignment:', err);
    }
  };

  const handleUpdateAssignment = async (id: string, data: Partial<AITaskAssignment>) => {
    try {
      await updateTaskAssignment(id, data);
      setEditingId(null);
      setEditData({});
      setIsDropdownOpen(null);
    } catch (err) {
      console.error('Failed to update assignment:', err);
    }
  };

  const handleEditChange = (field: string, value: any) => {
    setEditData(prev => ({ ...prev, [field]: value }));
  };

  const handleSaveEdit = (id: string) => {
    handleUpdateAssignment(id, editData);
  };

  const handleDeleteAssignment = async (id: string) => {
    showConfirmation({
      title: 'Remove Assignment',
      message: 'Are you sure you want to remove this assignment? The system will use the default fallback.',
      confirmLabel: 'Remove Assignment',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteTaskAssignment(id);
          setEditingId(null);
          setEditData({});
        } catch (err) {
          console.error('Failed to delete assignment:', err);
        }
      }
    });
  };

  return (
    <div className="space-y-4 max-w-3xl pb-60">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-dark-text">
          Task Assignments
        </h3>
      </div>

      {error && (
        <div className="p-3 bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-lg flex items-center justify-between">
          <span>{error}</span>
          <button onClick={clearError} className="text-sm underline font-bold">Dismiss</button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4">
        {TASK_TYPES.map((taskType) => {
          const assignment = getAssignmentForTaskType(taskType.value);
          const currentProvider = providers.find(p => assignment && p.id === assignment.provider_id);
          const currentModel = models.find(m => assignment && m.id === assignment.model_id);
          
          const isEditing = editingId === assignment?.id;
          const isOpen = isDropdownOpen === (assignment?.id || `new-${taskType.value}`);
          const isGlobalDefault = taskType.value === 'default';

          const canConfigure = (assignment?.user_id === userId) || 
                              (scope === 'global' && !assignment?.user_id && !assignment?.tenant_id) ||
                              (scope === 'tenant' && assignment?.tenant_id === tenantId && !assignment?.user_id) ||
                              (!assignment); // Can always try to create a new one in current scope

          return (
            <div
              key={taskType.value}
              className={`p-4 rounded-xl border transition-all group ${
                isGlobalDefault 
                  ? 'bg-blue-50/30 dark:bg-blue-900/5 border-blue-200 dark:border-blue-900/30 shadow-sm mb-6' 
                  : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border hover:border-blue-300'
              } ${isEditing ? 'border-blue-500 ring-1 ring-blue-500/20 shadow-md' : 'cursor-pointer'}`}
              onClick={() => {
                if (!isEditing && assignment && canConfigure) {
                  setEditingId(assignment.id);
                  setEditData(assignment);
                } else if (!assignment && canConfigure) {
                  setIsDropdownOpen(`new-${taskType.value}`);
                  setSearchTerm('');
                }
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-3">
                  <div className={`p-2 rounded-lg transition-colors ${
                    isGlobalDefault 
                      ? 'bg-blue-600 text-white shadow-blue-200' 
                      : isEditing 
                        ? 'bg-blue-600 text-white' 
                        : 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 group-hover:bg-blue-100'
                  }`}>
                    {(() => { const Icon = taskType.icon; return <Icon className="w-5 h-5" />; })()}
                  </div>
                  <div>
                    <h4 className={`text-md font-bold flex items-center gap-2 ${isGlobalDefault ? 'text-blue-900 dark:text-blue-400' : 'text-gray-900 dark:text-dark-text'}`}>
                      {taskType.label}
                      
                      {assignment?.user_id && (
                        <span className="flex items-center gap-1 px-1.5 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400 text-[8px] font-black uppercase tracking-tighter rounded">
                          <User className="w-2 h-2" /> Mine
                        </span>
                      )}

                      {isGlobalDefault && (
                        <span className="ml-2 px-2 py-0.5 bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 text-[9px] font-black uppercase tracking-tighter rounded">
                          System Fallback
                        </span>
                      )}
                    </h4>
                    {assignment ? (
                      <p className={`text-sm font-medium ${isGlobalDefault ? 'text-blue-600 dark:text-blue-300' : 'text-blue-600 dark:text-blue-400'}`}>
                        {currentProvider?.name} / {currentModel?.name || 'Default Model'}
                      </p>
                    ) : (
                      <p className="text-sm text-gray-500 dark:text-gray-400 italic">Not assigned - will use {scope === 'user' ? 'organization' : 'system'} default</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  {!assignment && (
                    <span className="opacity-0 group-hover:opacity-100 transition-opacity px-2 py-0.5 bg-gray-100 dark:bg-dark-bg text-gray-400 text-[9px] font-bold uppercase tracking-wider rounded-md border border-gray-200 dark:border-dark-border">
                      Click to Assign
                    </span>
                  )}
                  {assignment && !isEditing && (
                    <div className="opacity-0 group-hover:opacity-100 transition-all flex items-center space-x-1">
                      {canConfigure && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteAssignment(assignment.id);
                          }}
                          className="p-1.5 text-gray-300 hover:text-red-400 transition-colors"
                          title="Remove assignment"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                      <div className="px-2 py-0.5 bg-gray-50 dark:bg-dark-bg text-[9px] font-bold uppercase tracking-tight text-gray-400 rounded border border-gray-100 dark:border-dark-border">
                        {canConfigure ? 'Configure' : 'Inherited'}
                      </div>
                    </div>
                  )}

                  {isEditing && (
                    <SettingsIcon className="w-4 h-4 text-blue-500/50 animate-spin-slow" />
                  )}
                </div>
              </div>

              {(isEditing || isOpen) && (
                <div 
                  className="mt-4 pt-4 border-t border-gray-100 dark:border-dark-border space-y-4"
                  onClick={(e) => e.stopPropagation()}
                >
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                      Select Provider & Model
                    </label>
                    <div className="relative">
                      <div 
                        className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 bg-white dark:bg-dark-surface outline-none flex items-center justify-between cursor-pointer"
                        onClick={() => setIsDropdownOpen(isOpen ? null : (assignment?.id || `new-${taskType.value}`))}
                      >
                        <span className={(assignment || editData.model_id) ? "text-gray-900 dark:text-dark-text" : "text-gray-400"}>
                          {isEditing 
                            ? ((providers.find(p => p.id === editData.provider_id)?.name || "Select Provider") + " / " + (models.find(m => m.id === editData.model_id)?.name || "Select Model"))
                            : "Choose a model..."
                          }
                        </span>
                        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                      </div>

                      {isOpen && (
                        <div className="absolute z-20 w-full mt-1 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
                          <div className="p-2 border-b border-gray-100 dark:border-dark-border sticky top-0 bg-white dark:bg-dark-surface">
                            <div className="relative">
                              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                              <input
                                type="text"
                                autoFocus
                                placeholder="Search models or providers..."
                                className="w-full pl-9 pr-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-md text-sm outline-none focus:ring-1 focus:ring-blue-500 dark:text-dark-text"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                onClick={(e) => e.stopPropagation()}
                              />
                            </div>
                          </div>
                          
                          <div className="max-h-60 overflow-y-auto">
                            {filteredGroupedModels.length > 0 ? (
                              filteredGroupedModels.map((group) => (
                                <div key={group.provider.id}>
                                  <div className="px-4 py-2 text-xs font-bold text-gray-400 uppercase tracking-wider bg-gray-50 dark:bg-dark-bg/50 border-y border-gray-100 dark:border-dark-border">
                                    {group.provider.name}
                                  </div>
                                  {group.models.map((m) => (
                                    <div
                                      key={m.id}
                                      className={`px-4 py-2.5 text-sm flex items-center justify-between cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors ${(isEditing ? editData.model_id === m.id : false) ? 'bg-blue-50 dark:bg-blue-900/10 text-blue-600 dark:text-blue-400' : 'text-gray-700 dark:text-dark-text'}`}
                                      onClick={() => {
                                        if (isEditing) {
                                          handleEditChange('provider_id', group.provider.id);
                                          handleEditChange('model_id', m.id);
                                        } else {
                                          handleCreateAssignment(taskType.value, group.provider.id, m.id);
                                        }
                                        setIsDropdownOpen(null);
                                        setSearchTerm('');
                                      }}
                                    >
                                      <div className="flex flex-col">
                                        <span className="font-medium">{m.name}</span>
                                        <span className="text-[10px] text-gray-400">{m.model_name}</span>
                                      </div>
                                      {(isEditing ? editData.model_id === m.id : false) && <Check className="w-4 h-4" />}
                                    </div>
                                  ))}
                                </div>
                              ))
                            ) : (
                              <div className="px-4 py-6 text-sm text-gray-400 italic text-center">
                                No models found matching "{searchTerm}"
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                      
                      {isOpen && (
                        <div 
                          className="fixed inset-0 z-10" 
                          onClick={() => setIsDropdownOpen(null)}
                        />
                      )}
                    </div>
                  </div>

                  {isEditing && (
                    <div className="flex items-center space-x-4">
                      <div className="flex items-center">
                        <input
                          type="checkbox"
                          id={`is_active-${assignment?.id}`}
                          checked={editData.is_active !== undefined ? editData.is_active : assignment?.is_active}
                          onChange={(e) => handleEditChange('is_active', e.target.checked)}
                          className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                        />
                        <label htmlFor={`is_active-${assignment?.id}`} className="ml-2 text-sm text-gray-700 dark:text-dark-text">
                          Assignment Active
                        </label>
                      </div>
                      
                      <div className="flex-1 flex justify-end space-x-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            if (assignment) handleDeleteAssignment(assignment.id);
                          }}
                          className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors mr-2"
                          title="Remove assignment"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => {
                            setEditingId(null);
                            setEditData({});
                          }}
                          className="px-4 py-1.5 text-sm font-medium text-gray-700 dark:text-dark-text hover:bg-gray-100 dark:hover:bg-dark-bg rounded-lg transition-colors"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={() => handleSaveEdit(assignment!.id)}
                          className="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors shadow-sm"
                        >
                          Save Changes
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
