import api from './axios';

export interface AIProvider {
  id: string;
  name: string;
  scope: 'SYSTEM' | 'TENANT' | 'USER';
  provider_type: string;
  api_base: string;
  api_key?: string;
  is_active: boolean;
  settings?: Record<string, any>;
  is_local?: boolean;
  company_name?: string;
  company_website?: string;
  company_country?: string;
  tenant_id?: string;
  user_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AIModel {
  id: string;
  provider_id: string;
  provider_name?: string;
  name: string;
  model_name: string;
  description?: string;
  is_active: boolean;
  max_tokens: number;
  temperature: number;
  is_local?: boolean;
  settings?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
}

export interface AITaskAssignment {
  id: string;
  task_type: string;
  scope: 'SYSTEM' | 'TENANT' | 'USER';
  provider_id?: string;
  provider_name?: string;
  model_id?: string;
  model_name?: string;
  is_active: boolean;
  priority: number;
  tenant_id?: string;
  user_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AIProviderCreate {
  name: string;
  scope?: 'SYSTEM' | 'TENANT' | 'USER';
  provider_type: string;
  api_base: string;
  api_key?: string;
  is_active?: boolean;
  settings?: Record<string, any>;
  tenant_id?: string;
  user_id?: string;
}

export interface AIModelCreate {
  provider_id: string;
  name: string;
  model_name: string;
  description?: string;
  is_active?: boolean;
  max_tokens?: number;
  temperature?: number;
  is_local?: boolean;
  settings?: Record<string, any>;
}

export interface AITaskAssignmentCreate {
  task_type: string;
  scope?: 'SYSTEM' | 'TENANT' | 'USER';
  provider_id?: string;
  model_id?: string;
  is_active?: boolean;
  priority?: number;
  tenant_id?: string;
  user_id?: string;
}

export interface AIConfigSummary {
  providers: AIProvider[];
  models: AIModel[];
  task_assignments: AITaskAssignment[];
  default?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  ocr?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  nlp?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  medication_interaction?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  anomaly_detection?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  fill_biomarker_form?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  fill_medication_form?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  magic_fill_examination?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  define_biomarker?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  define_medication?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  suggest_category_icon?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  generate_category_icon?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  chat?: {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  };
  workflows?: Record<string, {
    task_type: string;
    provider?: AIProvider;
    model?: AIModel;
    assignment_id?: string;
  }[]>;
}

// Provider endpoints
export const aiConfigApi = {
  // Providers
  createProvider: async (data: AIProviderCreate): Promise<AIProvider> => {
    const response = await api.post('/ai-config/providers', data);
    return response.data;
  },

  getProviders: async (
    tenant_id?: string,
    user_id?: string,
    is_active?: boolean,
    include_models?: boolean,
    scope?: string
  ): Promise<AIProvider[]> => {
    const params = new URLSearchParams();
    if (tenant_id) params.append('tenant_id', tenant_id);
    if (user_id) params.append('user_id', user_id);
    if (is_active !== undefined) params.append('is_active', String(is_active));
    if (include_models) params.append('include_models', String(include_models));
    if (scope) params.append('scope', scope);
    
    const response = await api.get(`/ai-config/providers?${params}`);
    return response.data;
  },

  getProvider: async (provider_id: string): Promise<AIProvider> => {
    const response = await api.get(`/ai-config/providers/${provider_id}`);
    return response.data;
  },

  getProviderWithModels: async (provider_id: string): Promise<AIProvider & { models: AIModel[] }> => {
    const response = await api.get(`/ai-config/providers/${provider_id}/with-models`);
    return response.data;
  },

  updateProvider: async (provider_id: string, data: Partial<AIProviderCreate>): Promise<AIProvider> => {
    const response = await api.put(`/ai-config/providers/${provider_id}`, data);
    return response.data;
  },

  deleteProvider: async (provider_id: string): Promise<void> => {
    await api.delete(`/ai-config/providers/${provider_id}`);
  },

  fetchExternalModels: async (provider_id: string): Promise<any[]> => {
    const response = await api.get(`/ai-config/providers/${provider_id}/fetch-external-models`);
    return response.data;
  },

  // Models
  createModel: async (provider_id: string, data: AIModelCreate): Promise<AIModel> => {
    const response = await api.post(`/ai-config/providers/${provider_id}/models`, data);
    return response.data;
  },

  getModelsForProvider: async (provider_id: string, is_active?: boolean): Promise<AIModel[]> => {
    const params = new URLSearchParams();
    if (is_active !== undefined) params.append('is_active', String(is_active));
    
    const response = await api.get(`/ai-config/providers/${provider_id}/models?${params}`);
    return response.data;
  },

  getModel: async (model_id: string): Promise<AIModel> => {
    const response = await api.get(`/ai-config/models/${model_id}`);
    return response.data;
  },

  updateModel: async (model_id: string, data: Partial<AIModelCreate>): Promise<AIModel> => {
    const response = await api.put(`/ai-config/models/${model_id}`, data);
    return response.data;
  },

  deleteModel: async (model_id: string): Promise<void> => {
    await api.delete(`/ai-config/models/${model_id}`);
  },

  // Task Assignments
  createTaskAssignment: async (data: AITaskAssignmentCreate): Promise<AITaskAssignment> => {
    const response = await api.post('/ai-config/task-assignments', data);
    return response.data;
  },

  getTaskAssignments: async (
    tenant_id?: string,
    user_id?: string,
    task_type?: string,
    is_active?: boolean,
    scope?: string
  ): Promise<AITaskAssignment[]> => {
    const params = new URLSearchParams();
    if (tenant_id) params.append('tenant_id', tenant_id);
    if (user_id) params.append('user_id', user_id);
    if (task_type) params.append('task_type', task_type);
    if (is_active !== undefined) params.append('is_active', String(is_active));
    if (scope) params.append('scope', scope);
    
    const response = await api.get(`/ai-config/task-assignments?${params}`);
    return response.data;
  },

  getTaskAssignment: async (assignment_id: string): Promise<AITaskAssignment> => {
    const response = await api.get(`/ai-config/task-assignments/${assignment_id}`);
    return response.data;
  },

  updateTaskAssignment: async (
    assignment_id: string,
    data: Partial<AITaskAssignmentCreate>
  ): Promise<AITaskAssignment> => {
    const response = await api.put(`/ai-config/task-assignments/${assignment_id}`, data);
    return response.data;
  },

  deleteTaskAssignment: async (assignment_id: string): Promise<void> => {
    await api.delete(`/ai-config/task-assignments/${assignment_id}`);
  },

  getActiveTaskAssignment: async (task_type: string, tenant_id?: string, user_id?: string): Promise<AITaskAssignment> => {
    const params = new URLSearchParams();
    if (tenant_id) params.append('tenant_id', tenant_id);
    if (user_id) params.append('user_id', user_id);
    
    const response = await api.get(`/ai-config/task-assignments/active/${task_type}?${params}`);
    return response.data;
  },

  // Summary
  getConfigSummary: async (tenant_id?: string, user_id?: string, scope?: string): Promise<AIConfigSummary> => {
    const params = new URLSearchParams();
    if (tenant_id) params.append('tenant_id', tenant_id);
    if (user_id) params.append('user_id', user_id);
    if (scope) params.append('scope', scope);
    
    const response = await api.get(`/ai-config/summary?${params}`);
    return response.data;
  },

  getDefaultForTask: async (task_type: string, tenant_id?: string, user_id?: string): Promise<{
    provider: AIProvider;
    model?: AIModel;
  }> => {
    const params = new URLSearchParams();
    if (tenant_id) params.append('tenant_id', tenant_id);
    if (user_id) params.append('user_id', user_id);
    
    const response = await api.get(`/ai-config/default-for-task/${task_type}?${params}`);
    return response.data;
  },
};