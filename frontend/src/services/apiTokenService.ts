/**
 * Scoped personal API tokens (program 12C2B, ADR 0054 Phase 6).
 *
 * The plaintext is returned exactly once (create and rotate); every other
 * read is hash-free. The server re-validates the permission subset and the
 * patient scope on every write, so the picker is a convenience, not the
 * gate. Tokens never appear in logs or local storage.
 */
import api from '../api/axios';

export type ApiTokenPatientScope = 'all' | 'granted' | 'none';

export interface ApiToken {
  id: string;
  tenant_id: string;
  owner_user_id: string;
  name: string;
  token_prefix: string;
  permissions: string[];
  patient_scope: ApiTokenPatientScope;
  patient_ids: string[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  rotated_from: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ApiTokenCreated {
  token: string;
  api_token: ApiToken;
}

export interface ApiTokenPermissionOption {
  permission: string;
  scope: string;
}

export interface ApiTokenPermissionCatalog {
  permissions: ApiTokenPermissionOption[];
  patient_scopes: string[];
}

export interface ApiTokenCreatePayload {
  name: string;
  permissions: string[];
  patient_scope?: ApiTokenPatientScope;
  patient_ids?: string[];
  expires_at?: string | null;
}

export type ApiTokenUpdatePayload = Partial<ApiTokenCreatePayload>;

export async function listApiTokens(): Promise<ApiToken[]> {
  const response = await api.get<ApiToken[]>('/me/api-tokens');
  return Array.isArray(response.data) ? response.data : [];
}

export async function getApiTokenPermissions(): Promise<ApiTokenPermissionCatalog> {
  const response = await api.get<ApiTokenPermissionCatalog>('/me/api-tokens/permissions');
  return response.data;
}

export async function createApiToken(
  payload: ApiTokenCreatePayload,
): Promise<ApiTokenCreated> {
  const response = await api.post<ApiTokenCreated>('/me/api-tokens', payload);
  return response.data;
}

export async function updateApiToken(
  tokenId: string,
  payload: ApiTokenUpdatePayload,
): Promise<ApiToken> {
  const response = await api.patch<ApiToken>(`/me/api-tokens/${tokenId}`, payload);
  return response.data;
}

export async function rotateApiToken(tokenId: string): Promise<ApiTokenCreated> {
  const response = await api.post<ApiTokenCreated>(`/me/api-tokens/${tokenId}/rotate`);
  return response.data;
}

export async function revokeApiToken(tokenId: string): Promise<void> {
  await api.delete(`/me/api-tokens/${tokenId}`);
}
