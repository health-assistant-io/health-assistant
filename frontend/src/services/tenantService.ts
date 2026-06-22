import api from '../api/axios';

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  is_active: boolean;
  owner_id?: string | null;
  settings: Record<string, any>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TenantStats {
  users_count: number;
  active_users_count: number;
  patients_count: number;
  organizations_count: number;
  examinations_count: number;
  observations_count: number;
  documents_count: number;
  storage_bytes: number;
}

export interface UserSummary {
  id: string;
  email: string;
  role: string;
}

export interface TenantDetail extends Tenant {
  stats: TenantStats;
  owner?: UserSummary | null;
}

export interface TenantListResponse {
  items: Tenant[];
  total: number;
}

export interface TenantUser {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  tenant_id: string;
  created_at?: string | null;
}

export interface TenantUserListResponse {
  items: TenantUser[];
  total: number;
}

export interface UpdateTenantUserPayload {
  role?: 'USER' | 'MANAGER' | 'ADMIN';
  is_active?: boolean;
}

export interface CreateInvitePayload {
  email?: string;
  role?: 'USER' | 'MANAGER' | 'ADMIN';
  expires_days?: number;
}

export interface InviteResult {
  invite_token: string;
  tenant_id: string;
  role: string;
  expires_in_days: number;
}

export interface SwitchTenantResult {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  scoped_tenant_id: string;
  original_tenant_id: string;
  tenant: Tenant;
}

export interface AuditEntry {
  id: string;
  user_id?: string | null;
  action: string;
  resource_type: string;
  resource_id?: string | null;
  old_value?: Record<string, any> | null;
  new_value?: Record<string, any> | null;
  created_at?: string | null;
}

export interface AuditListResponse {
  items: AuditEntry[];
  total: number;
}

export interface ListParams {
  search?: string;
  limit?: number;
  offset?: number;
  is_active?: boolean;
}

function toQuery(params: Record<string, any>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.append(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

// ---- Admin surface (/admin/tenants) ----

export async function listTenants(params: ListParams = {}): Promise<TenantListResponse> {
  const r = await api.get<TenantListResponse>(`/admin/tenants${toQuery(params as any)}`);
  return r.data;
}

export async function getTenantDetail(tenantId: string): Promise<TenantDetail> {
  const r = await api.get<TenantDetail>(`/admin/tenants/${tenantId}`);
  return r.data;
}

export async function createTenant(payload: {
  name: string;
  slug?: string;
  description?: string;
  settings?: Record<string, any>;
  owner_id?: string;
}): Promise<Tenant> {
  const r = await api.post<Tenant>('/admin/tenants', payload);
  return r.data;
}

export async function updateTenant(
  tenantId: string,
  patch: {
    name?: string;
    slug?: string;
    description?: string;
    settings?: Record<string, any>;
  }
): Promise<Tenant> {
  const r = await api.patch<Tenant>(`/admin/tenants/${tenantId}`, patch);
  return r.data;
}

export async function deactivateTenant(tenantId: string): Promise<Tenant> {
  const r = await api.post<Tenant>(`/admin/tenants/${tenantId}/deactivate`);
  return r.data;
}

export async function reactivateTenant(tenantId: string): Promise<Tenant> {
  const r = await api.post<Tenant>(`/admin/tenants/${tenantId}/reactivate`);
  return r.data;
}

export async function hardDeleteTenant(
  tenantId: string,
  confirmName: string
): Promise<{ message: string }> {
  const r = await api.request<{ message: string }>({
    method: 'DELETE',
    url: `/admin/tenants/${tenantId}`,
    data: { permanent: true, confirm_name: confirmName },
  });
  return r.data;
}

export async function switchIntoTenant(tenantId: string): Promise<SwitchTenantResult> {
  const r = await api.post<SwitchTenantResult>(`/admin/tenants/${tenantId}/switch`);
  return r.data;
}

export async function exitTenantSwitch(): Promise<SwitchTenantResult> {
  const r = await api.post<SwitchTenantResult>('/admin/tenants/exit-switch');
  return r.data;
}

// ---- Per-tenant user management ----

export async function listTenantUsers(
  tenantId: string,
  params: ListParams = {}
): Promise<TenantUserListResponse> {
  const r = await api.get<TenantUserListResponse>(
    `/admin/tenants/${tenantId}/users${toQuery(params as any)}`
  );
  return r.data;
}

export async function updateTenantUser(
  tenantId: string,
  userId: string,
  payload: UpdateTenantUserPayload
): Promise<TenantUser> {
  const r = await api.patch<TenantUser>(
    `/admin/tenants/${tenantId}/users/${userId}`,
    payload
  );
  return r.data;
}

export async function createTenantInvite(
  tenantId: string,
  payload: CreateInvitePayload
): Promise<InviteResult> {
  const r = await api.post<InviteResult>(`/admin/tenants/${tenantId}/invite`, payload);
  return r.data;
}

// ---- Audit viewer ----

export async function listTenantAudit(
  tenantId: string,
  params: ListParams & { action?: string } = {}
): Promise<AuditListResponse> {
  const r = await api.get<AuditListResponse>(
    `/admin/tenants/${tenantId}/audit${toQuery(params as any)}`
  );
  return r.data;
}

// ---- Self-info (caller's own tenant) ----

export async function getMyTenant(): Promise<Tenant> {
  const r = await api.get<Tenant>('/tenants');
  return r.data;
}

export async function getTenant(tenantId: string): Promise<Tenant> {
  if (!tenantId || tenantId === 'undefined') {
    throw new Error('Invalid Tenant ID');
  }
  const r = await api.get<Tenant>(`/tenants/${tenantId}`);
  return r.data;
}
