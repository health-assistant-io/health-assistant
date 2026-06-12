import api from '../api/axios';

export interface Tenant {
  id: string;
  name: string;
  settings: Record<string, any>;
  created_at?: string;
}

export async function listTenants(): Promise<Tenant[]> {
  const response = await api.get<Tenant[]>('/tenants');
  return response.data;
}

export async function getTenant(tenantId: string): Promise<Tenant> {
  if (!tenantId || tenantId === 'undefined') {
    throw new Error('Invalid Tenant ID');
  }
  const response = await api.get<Tenant>(`/tenants/${tenantId}`);
  return response.data;
}

export async function createTenant(name: string, settings: Record<string, any> = {}): Promise<Tenant> {
  const response = await api.post<Tenant>(`/tenants?name=${encodeURIComponent(name)}`, settings);
  return response.data;
}

export async function updateTenant(tenantId: string, name?: string, settings?: Record<string, any>): Promise<Tenant> {
  let url = `/tenants/${tenantId}`;
  const params = [];
  if (name) params.push(`name=${encodeURIComponent(name)}`);
  
  if (params.length > 0) {
    url += `?${params.join('&')}`;
  }
  
  const response = await api.put<Tenant>(url, settings);
  return response.data;
}

export async function deleteTenant(tenantId: string): Promise<{ message: string }> {
  const response = await api.delete<{ message: string }>(`/tenants/${tenantId}`);
  return response.data;
}
