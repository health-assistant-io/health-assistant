import api from '../api/axios';
import { Organization } from '../types/clinical';

export type { Organization };

export async function listOrganizations(): Promise<Organization[]> {
  const response = await api.get<Organization[]>('/organizations');
  return response.data;
}

export async function getOrganization(organizationId: string): Promise<Organization> {
  const response = await api.get<Organization>(`/organizations/${organizationId}`);
  return response.data;
}

export async function createOrganization(data: Partial<Organization>): Promise<Organization> {
  const response = await api.post<Organization>('/organizations', data);
  return response.data;
}

export async function updateOrganization(
  organizationId: string, 
  data: Partial<Organization> & { doctor_ids?: string[] }
): Promise<Organization> {
  const response = await api.put<Organization>(`/organizations/${organizationId}`, data);
  return response.data;
}

export async function deleteOrganization(organizationId: string): Promise<void> {
  await api.delete(`/organizations/${organizationId}`);
}
