/**
 * Own-session (device family) surface — ``/me/sessions`` (identity-auth §12).
 *
 * One row per signed-in device family; ``current`` marks the family the
 * calling browser belongs to. Revoking a family kills its refresh chain — and
 * the bearer credential too when it is the caller's own, so the UI must treat
 * that call as a logout.
 */
import api from '../api/axios';

export interface UserSession {
  id: string;
  client_label: string;
  created_at: string | null;
  expires_at: string | null;
  absolute_expires_at: string | null;
  revoked_at: string | null;
  current: boolean;
}

export async function listMySessions(): Promise<UserSession[]> {
  const res = await api.get<UserSession[]>('/me/sessions');
  return res.data;
}

export async function revokeMySession(sessionId: string): Promise<void> {
  await api.delete(`/me/sessions/${sessionId}`);
}
