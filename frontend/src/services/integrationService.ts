import api from '../api/axios';

export interface IntegrationManifest {
  domain: string;
  name: string;
  version: string;
  integration_type: string[];
  description?: string;
  author?: string;
  access_type?: 'local' | 'cloud' | 'hybrid';
  categories?: string[];
  icon?: string;
  dependencies?: string[];
}

export interface ActiveIntegration {
  id: string;
  domain: string;
  instance_name: string | null;
  status: string;
  last_synced_at: string | null;
}

export interface ConfigFlowSchema {
  step_id: string;
  title: string;
  description?: string;
  data_schema: any;
}

export interface CustomAction {
  id: string;
  label: string;
  style: 'primary' | 'danger' | 'warning' | 'default';
}

// --- Structured display blocks for custom action results ---
// See integrations/sdk/display.py for the canonical contract.

export type DisplayBlockType = 'kv' | 'list' | 'table' | 'json' | 'text' | 'code';

export interface KvBlock {
  type: 'kv';
  title?: string;
  items: Record<string, any>;
}

export interface ListBlock {
  type: 'list';
  title?: string;
  items: string[];
}

export interface TableBlock {
  type: 'table';
  title?: string;
  columns: string[];
  rows: any[][];
}

export interface JsonBlock {
  type: 'json';
  title?: string;
  data: any;
}

export interface TextBlock {
  type: 'text';
  title?: string;
  content: string;
}

export interface CodeBlock {
  type: 'code';
  title?: string;
  content: string;
  language?: string;
}

export type DisplayBlock =
  | KvBlock
  | ListBlock
  | TableBlock
  | JsonBlock
  | TextBlock
  | CodeBlock
  | { type: string; [key: string]: any }; // unknown types fall back to JSON

export interface ActionResult {
  message?: string;
  results?: DisplayBlock[];
  [key: string]: any;
}

export interface IntegrationDocsTreeItem {
  id: string;
  file: string;
  title: string;
}

export interface IntegrationDocsTreeCategory {
  category: string;
  items: IntegrationDocsTreeItem[];
}

export interface IntegrationDocumentation {
  markdown: string;
  tree?: IntegrationDocsTreeCategory[];
}

export const integrationService = {
  getAvailable: async (): Promise<IntegrationManifest[]> => {
    const response = await api.get('/integrations/available');
    return response.data;
  },

  getActive: async (patientId: string): Promise<ActiveIntegration[]> => {
    const response = await api.get(`/integrations/active?patient_id=${patientId}`);
    return response.data;
  },

  getConfigFlow: async (domain: string): Promise<ConfigFlowSchema> => {
    const response = await api.get(`/integrations/${domain}/config-flow`);
    return response.data;
  },

  getDocumentation: async (domain: string, file?: string): Promise<IntegrationDocumentation> => {
    const url = file ? `/integrations/${domain}/documentation?file=${encodeURIComponent(file)}` : `/integrations/${domain}/documentation`;
    const response = await api.get(url);
    return response.data;
  },

  getDetails: async (integrationId: string, patientId: string): Promise<any> => {
    const response = await api.get(`/integrations/instance/${integrationId}/details?patient_id=${patientId}`);
    return response.data;
  },

  submitConfigFlow: async (domain: string, patientId: string, data: any, integrationId?: string): Promise<any> => {
    let url = `/integrations/${domain}/config-flow?patient_id=${patientId}`;
    if (integrationId) {
      url += `&integration_id=${integrationId}`;
    }
    const response = await api.post(url, data);
    return response.data;
  },

  syncIntegration: async (integrationId: string, patientId: string): Promise<any> => {
    const response = await api.post(`/integrations/instance/${integrationId}/sync?patient_id=${patientId}`);
    return response.data;
  },

  removeIntegration: async (integrationId: string, patientId: string): Promise<void> => {
    await api.delete(`/integrations/instance/${integrationId}?patient_id=${patientId}`);
  },

  executeAction: async (integrationId: string, patientId: string, actionId: string): Promise<any> => {
    const response = await api.post(`/integrations/instance/${integrationId}/action/${actionId}?patient_id=${patientId}`);
    return response.data;
  },

  getDebugLogs: async (integrationId: string, patientId: string): Promise<any[]> => {
    const response = await api.get(`/integrations/instance/${integrationId}/debug-logs?patient_id=${patientId}`);
    return response.data;
  },

  toggleDebugMode: async (integrationId: string, patientId: string): Promise<any> => {
    const response = await api.post(`/integrations/instance/${integrationId}/toggle-debug?patient_id=${patientId}`);
    return response.data;
  }
};
