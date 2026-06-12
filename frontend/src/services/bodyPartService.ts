import api from '../api/axios';

export interface BodyPart {
  id: string;
  name: string;
  slug: string;
  snomed_code?: string;
  description?: string;
  is_custom: boolean;
  tenant_id?: string;
}

export interface BodyPartCreate {
  name: string;
  snomed_code?: string;
  description?: string;
}

export const listBodyParts = async (): Promise<BodyPart[]> => {
  const response = await api.get('/body-parts');
  return response.data;
};

export const createBodyPart = async (data: BodyPartCreate): Promise<BodyPart> => {
  const response = await api.post('/body-parts', data);
  return response.data;
};

export const getBodyPart = async (id: string): Promise<BodyPart> => {
  const response = await api.get(`/body-parts/${id}`);
  return response.data;
};
