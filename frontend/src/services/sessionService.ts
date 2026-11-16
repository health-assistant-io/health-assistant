/**
 * Self-service identity surface (identity-auth §12): the own-session device
 * family (``/me/sessions``) and the account credential (``/me/password``).
 *
 * One row per signed-in device family; ``current`` marks the family the
 * calling browser belongs to. Revoking a family kills its refresh chain — and
 * the bearer credential too when it is the caller's own, so the UI must treat
 * that call as a logout. A password change bumps ``token_version`` and
 * revokes EVERY family (§12), so the caller must sign in again.
 */
import api from '../api/axios';

export interface UserSession {
  id: string;
  client_label: string;
  ip: string | null;
  user_agent: string | null;
  last_seen_at: string | null;
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

/** Sign out every other device while keeping this sign-in family live. */
export async function revokeOtherSessions(): Promise<number> {
  const response = await api.post<{ revoked: number }>('/me/sessions/revoke-others');
  return response.data?.revoked ?? 0;
}

/**
 * Change the caller's password (`PATCH /me/password`). 204 on success — the
 * backend revokes every token family, so the UI logs out after the call.
 * 400 wrong current password, 422 policy failure, 429 lockout/rate limit.
 */
export async function changeMyPassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await api.patch('/me/password', {
    current_password: currentPassword,
    new_password: newPassword,
  });
}
