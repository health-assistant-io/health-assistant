import api from '../api/axios';

/**
 * The care-team / patient access surface (ADR 0054 Phase 2).
 *
 * Wire shapes mirror the backend `GrantResponse` / `MeGrantItem`
 * (`app/schemas/patient_access.py`). All enforcement is server-side —
 * these calls surface 403/404/409/422 as plain `detail` strings that the
 * UI shows via toasts (the mirror decides only what to hide/disable).
 */

export type GrantPermission = 'read' | 'write' | 'admin';
export type GrantSource = 'admin' | 'care_team' | 'patient' | 'guardian';
export type GrantStatus = 'pending' | 'active' | 'revoked' | 'expired' | 'declined';

export interface AccessGrant {
  id: string;
  patient_id: string;
  grantee_user_id?: string | null;
  grantee_doctor_id?: string | null;
  permission: GrantPermission;
  source: GrantSource;
  status: GrantStatus;
  granted_by?: string | null;
  granted_at?: string | null;
  accepted_at?: string | null;
  expires_at?: string | null;
  reason?: string | null;
  grantee_label?: string | null;
}

export interface MeGrant {
  id: string;
  patient_id: string;
  patient_name?: string | null;
  permission: GrantPermission;
  source: GrantSource;
  status: GrantStatus;
  granted_at?: string | null;
  accepted_at?: string | null;
  expires_at?: string | null;
}

export interface MyGrantsResponse {
  active: MeGrant[];
  pending: MeGrant[];
}

export interface CreateGrantPayload {
  grantee_user_id?: string;
  grantee_doctor_id?: string;
  permission?: GrantPermission;
  source?: GrantSource;
  expires_at?: string | null;
  reason?: string | null;
}

export interface UpdateGrantPayload {
  permission?: GrantPermission;
  expires_at?: string | null;
  reason?: string | null;
}

export async function listPatientAccess(patientId: string): Promise<AccessGrant[]> {
  const r = await api.get<{ items: AccessGrant[] }>(`/patients/${patientId}/access`);
  return r.data.items ?? [];
}

export async function createPatientAccess(
  patientId: string,
  payload: CreateGrantPayload
): Promise<AccessGrant> {
  const r = await api.post<AccessGrant>(`/patients/${patientId}/access`, payload);
  return r.data;
}

export async function updatePatientAccess(
  patientId: string,
  grantId: string,
  payload: UpdateGrantPayload
): Promise<AccessGrant> {
  const r = await api.patch<AccessGrant>(`/patients/${patientId}/access/${grantId}`, payload);
  return r.data;
}

export async function revokePatientAccess(patientId: string, grantId: string): Promise<AccessGrant> {
  const r = await api.delete<AccessGrant>(`/patients/${patientId}/access/${grantId}`);
  return r.data;
}

export async function getMyGrants(): Promise<MyGrantsResponse> {
  const r = await api.get<MyGrantsResponse>('/me/access/grants');
  return { active: r.data.active ?? [], pending: r.data.pending ?? [] };
}

export async function acceptMyGrant(grantId: string): Promise<{ id: string; status: string }> {
  const r = await api.post<{ id: string; status: string }>(
    `/me/access/grants/${grantId}/accept`
  );
  return r.data;
}

export async function declineMyGrant(
  grantId: string,
  reason?: string
): Promise<{ id: string; status: string }> {
  const r = await api.post<{ id: string; status: string }>(
    `/me/access/grants/${grantId}/decline`,
    reason ? { reason } : null
  );
  return r.data;
}

const PERMISSION_RANK: Record<GrantPermission, number> = { read: 0, write: 1, admin: 2 };

/** Whether a grant's permission satisfies the required level (read < write < admin). */
export function grantSatisfies(
  permission: GrantPermission | undefined | null,
  required: GrantPermission
): boolean {
  if (!permission) return false;
  return PERMISSION_RANK[permission] >= PERMISSION_RANK[required];
}
