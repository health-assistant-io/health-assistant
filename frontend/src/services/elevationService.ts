/**
 * Break-glass access elevations (program 12A, ADR 0054 D1).
 *
 * `/admin/access-elevations` is the two-person queue: request (step-up
 * gated), list, approve/deny (a second person), revoke. `/me/elevations`
 * is the caller's own ledger. Windows auto-expire; the server evaluates
 * `expires_at` on every request, so the client never caches reach.
 */
import api from '../api/axios';

export type ElevationScope = 'tenant' | 'system';
export type ElevationStatus = 'pending' | 'active' | 'expired' | 'revoked' | 'denied' | 'cancelled';

export interface Elevation {
  id: string;
  tenant_id: string | null;
  requested_by: string;
  approved_by: string | null;
  scope: ElevationScope;
  status: ElevationStatus;
  permissions: string[];
  reason: string;
  duration_minutes: number;
  requested_at: string;
  approved_at: string | null;
  starts_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  metadata: Record<string, unknown> | null;
}

export interface ElevationList {
  items: Elevation[];
  total: number;
}

export interface ElevationRequestPayload {
  scope: ElevationScope;
  permissions: string[];
  duration_minutes: number;
  reason: string;
  metadata?: Record<string, unknown>;
}

export interface ElevationListParams {
  tenantId?: string;
  scope?: ElevationScope;
  status?: ElevationStatus;
}

function toQuery(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.append(key, value);
  });
  const query = search.toString();
  return query ? `?${query}` : '';
}

export async function listElevations(params: ElevationListParams = {}): Promise<ElevationList> {
  const response = await api.get<ElevationList>(
    `/admin/access-elevations${toQuery({
      tenant_id: params.tenantId,
      scope: params.scope,
      status: params.status,
    })}`,
  );
  return {
    items: Array.isArray(response.data?.items) ? response.data.items : [],
    total: response.data?.total ?? 0,
  };
}

export async function listMyElevations(): Promise<Elevation[]> {
  const response = await api.get<Elevation[]>('/me/elevations');
  return Array.isArray(response.data) ? response.data : [];
}

export async function requestElevation(
  payload: ElevationRequestPayload,
): Promise<Elevation> {
  const response = await api.post<Elevation>('/admin/access-elevations', payload);
  return response.data;
}

export async function approveElevation(elevationId: string): Promise<Elevation> {
  const response = await api.post<Elevation>(
    `/admin/access-elevations/${elevationId}/approve`,
  );
  return response.data;
}

export async function denyElevation(
  elevationId: string,
  reason?: string,
): Promise<Elevation> {
  const response = await api.post<Elevation>(
    `/admin/access-elevations/${elevationId}/deny`,
    reason ? { reason } : {},
  );
  return response.data;
}

export async function revokeElevation(elevationId: string): Promise<Elevation> {
  const response = await api.post<Elevation>(
    `/admin/access-elevations/${elevationId}/revoke`,
  );
  return response.data;
}
