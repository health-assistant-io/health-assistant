import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { aiConfigApi, AIProvider, AIModel, AITaskAssignment, AIConfigSummary } from '../../api/aiConfig';

interface AIConfigState {
  providers: AIProvider[];
  models: AIModel[];
  taskAssignments: AITaskAssignment[];
  configSummary: AIConfigSummary | null;
  isLoading: boolean;
  error: string | null;
  
  // Actions for providers
  loadProviders: (tenant_id?: string, user_id?: string, scope?: string) => Promise<void>;
  createProvider: (data: { name: string; scope: 'SYSTEM' | 'TENANT' | 'USER'; provider_type: string; api_base: string; api_key?: string; tenant_id?: string; user_id?: string }) => Promise<AIProvider>;
  updateProvider: (id: string, data: Partial<AIProvider>) => Promise<AIProvider>;
  deleteProvider: (id: string) => Promise<void>;
  
  // Actions for models
  loadModels: (provider_id: string) => Promise<void>;
  createModel: (provider_id: string, data: any) => Promise<AIModel>;
  updateModel: (id: string, data: Partial<AIModel>) => Promise<AIModel>;
  deleteModel: (id: string) => Promise<void>;
  fetchExternalModels: (provider_id: string) => Promise<any[]>;
  
  // Actions for task assignments
  loadTaskAssignments: (tenant_id?: string, user_id?: string, scope?: string) => Promise<void>;
  createTaskAssignment: (data: any) => Promise<AITaskAssignment>;
  updateTaskAssignment: (id: string, data: Partial<AITaskAssignment>) => Promise<AITaskAssignment>;
  deleteTaskAssignment: (id: string) => Promise<void>;
  
  // Summary
  loadConfigSummary: (tenant_id?: string, user_id?: string, scope?: string) => Promise<void>;
  
  // Settings
  updateAISettings: (data: { ai_agent_max_iterations?: number }, tenant_id?: string, user_id?: string, scope?: string) => Promise<void>;
  
  // Utility
  clearError: () => void;
}

export const useAIConfigStore = create<AIConfigState>()(
  persist(
    (set, get) => ({
      providers: [],
      models: [],
      taskAssignments: [],
      configSummary: null,
      isLoading: false,
      error: null,
      
      // Provider actions
      loadProviders: async (tenant_id, user_id, scope) => {
        set({ isLoading: true, error: null });
        try {
          const providers = await aiConfigApi.getProviders(tenant_id, user_id, undefined, undefined, scope);
          set({ providers, isLoading: false });
        } catch (error: any) {
          set({ error: error.message || 'Failed to load providers', isLoading: false });
        }
      },
      
      createProvider: async (data) => {
        set({ error: null });
        try {
          const provider = await aiConfigApi.createProvider(data);
          set((state) => ({
            providers: [...state.providers, provider]
          }));
          return provider;
        } catch (error: any) {
          set({ error: error.message || 'Failed to create provider' });
          throw error;
        }
      },
      
      updateProvider: async (id, data) => {
        set({ error: null });
        try {
          const provider = await aiConfigApi.updateProvider(id, data);
          set((state) => ({
            providers: state.providers.map(p => p.id === id ? provider : p)
          }));
          return provider;
        } catch (error: any) {
          set({ error: error.message || 'Failed to update provider' });
          throw error;
        }
      },
      
      deleteProvider: async (id) => {
        set({ error: null });
        try {
          await aiConfigApi.deleteProvider(id);
          set((state) => ({
            providers: state.providers.filter(p => p.id !== id)
          }));
        } catch (error: any) {
          set({ error: error.message || 'Failed to delete provider' });
          throw error;
        }
      },
      
      // Model actions
      loadModels: async (provider_id) => {
        try {
          const newModels = await aiConfigApi.getModelsForProvider(provider_id);
          set((state) => {
            // Filter out existing models for this provider to avoid duplicates
            const otherModels = state.models.filter(m => m.provider_id !== provider_id);
            return { 
              models: [...otherModels, ...newModels]
            };
          });
        } catch (error: any) {
          set({ error: error.message || 'Failed to load models' });
        }
      },
      
      createModel: async (provider_id, data) => {
        set({ error: null });
        try {
          const model = await aiConfigApi.createModel(provider_id, { ...data, provider_id });
          set((state) => ({
            models: [...state.models, model]
          }));
          return model;
        } catch (error: any) {
          set({ error: error.message || 'Failed to create model' });
          throw error;
        }
      },
      
      updateModel: async (id, data) => {
        set({ error: null });
        try {
          const model = await aiConfigApi.updateModel(id, data);
          set((state) => ({
            models: state.models.map(m => m.id === id ? model : m)
          }));
          return model;
        } catch (error: any) {
          set({ error: error.message || 'Failed to update model' });
          throw error;
        }
      },
      
      deleteModel: async (id) => {
        set({ error: null });
        try {
          await aiConfigApi.deleteModel(id);
          set((state) => ({
            models: state.models.filter(m => m.id !== id)
          }));
        } catch (error: any) {
          set({ error: error.message || 'Failed to delete model' });
          throw error;
        }
      },
      
      fetchExternalModels: async (provider_id) => {
        try {
          const models = await aiConfigApi.fetchExternalModels(provider_id);
          return models;
        } catch (error: any) {
          set({ error: error.message || 'Failed to fetch external models' });
          throw error;
        }
      },
      
      // Task assignment actions
      loadTaskAssignments: async (tenant_id, user_id, scope) => {
        try {
          const assignments = await aiConfigApi.getTaskAssignments(tenant_id, user_id, undefined, undefined, scope);
          set({ taskAssignments: assignments });
        } catch (error: any) {
          set({ error: error.message || 'Failed to load task assignments' });
        }
      },
      
      createTaskAssignment: async (data) => {
        set({ error: null });
        try {
          const assignment = await aiConfigApi.createTaskAssignment(data);
          set((state) => ({
            taskAssignments: [...state.taskAssignments, assignment]
          }));
          return assignment;
        } catch (error: any) {
          set({ error: error.message || 'Failed to create task assignment' });
          throw error;
        }
      },
      
      updateTaskAssignment: async (id, data) => {
        set({ error: null });
        try {
          const assignment = await aiConfigApi.updateTaskAssignment(id, data);
          set((state) => ({
            taskAssignments: state.taskAssignments.map(a => a.id === id ? assignment : a)
          }));
          return assignment;
        } catch (error: any) {
          set({ error: error.message || 'Failed to update task assignment' });
          throw error;
        }
      },
      
      deleteTaskAssignment: async (id) => {
        set({ error: null });
        try {
          await aiConfigApi.deleteTaskAssignment(id);
          set((state) => ({
            taskAssignments: state.taskAssignments.filter(a => a.id !== id)
          }));
        } catch (error: any) {
          set({ error: error.message || 'Failed to delete task assignment' });
          throw error;
        }
      },
      
      // Summary
      loadConfigSummary: async (tenant_id, user_id, scope) => {
        set({ isLoading: true, error: null });
        try {
          const summary = await aiConfigApi.getConfigSummary(tenant_id, user_id, scope);
          set({ 
            configSummary: summary,
            providers: summary.providers,
            models: summary.models,
            taskAssignments: summary.task_assignments,
            isLoading: false 
          });
        } catch (error: any) {
          set({ error: error.message || 'Failed to load config summary', isLoading: false });
        }
      },
      
      updateAISettings: async (data, tenant_id, user_id, scope) => {
        set({ isLoading: true, error: null });
        try {
          await aiConfigApi.updateAISettings(data, tenant_id, user_id, scope);
          // Refresh summary after update
          const summary = await aiConfigApi.getConfigSummary(tenant_id, user_id, scope);
          set({ 
            configSummary: summary,
            isLoading: false 
          });
        } catch (error: any) {
          set({ error: error.message || 'Failed to update AI settings', isLoading: false });
          throw error;
        }
      },
      
      clearError: () => set({ error: null }),
    }),
    {
      name: 'ai-config-storage',
      partialize: (state) => ({
        providers: state.providers,
        models: state.models,
        taskAssignments: state.taskAssignments,
        configSummary: state.configSummary,
      }),
    }
  )
);