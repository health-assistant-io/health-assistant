import api from '../api/axios';

export interface IntegrationManifest {
  domain: string;
  name: string;
  version: string;
  integration_type: string[];
}

export interface ActiveIntegration {
  id: string;
  domain: string;
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

  getDetails: async (domain: string, patientId: string): Promise<any> => {
    const response = await api.get(`/integrations/${domain}/details?patient_id=${patientId}`);
    return response.data;
  },

  submitConfigFlow: async (domain: string, patientId: string, data: any): Promise<any> => {
    const response = await api.post(`/integrations/${domain}/config-flow?patient_id=${patientId}`, data);
    return response.data;
  },

  syncIntegration: async (domain: string, patientId: string): Promise<any> => {
    const response = await api.post(`/integrations/${domain}/sync?patient_id=${patientId}`);
    return response.data;
  },

  removeIntegration: async (domain: string, patientId: string): Promise<void> => {
    await api.delete(`/integrations/${domain}?patient_id=${patientId}`);
  },

  executeAction: async (domain: string, patientId: string, actionId: string): Promise<any> => {
    const response = await api.post(`/integrations/${domain}/action/${actionId}?patient_id=${patientId}`);
    return response.data;
  }
};
