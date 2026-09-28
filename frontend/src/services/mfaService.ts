/**
 * TOTP MFA API surface (plan 16 H5).
 *
 * Login-time endpoints (challenge flow) + self-service `/me/mfa*` +
 * the admin force surface. All calls ride the shared axios instance
 * (§10 cookies + CSRF echo).
 */
import api from '../api/axios';

/** Provisioning payload — the one-time enrollment response. */
export interface MFAEnrollment {
  secret: string;
  uri: string;
  recovery_codes: string[];
}

/** Self-service status (`GET /me/mfa`). */
export interface MFAStatus {
  enabled: boolean;
  enforced: boolean;
  pending: boolean;
}

/** The 401 login challenge body (`detail === 'mfa_required'`). */
export interface MFAChallengeBody {
  detail: 'mfa_required';
  mfa_token: string;
  enrollment_needed: boolean;
}

/** True when an axios error is the login MFA challenge (401 + body). */
export function isMfaChallenge(error: unknown): boolean {
  const data = mfaChallengeData(error);
  return (
    data?.detail === 'mfa_required' && typeof data?.mfa_token === 'string'
  );
}

function mfaChallengeData(error: unknown): Partial<MFAChallengeBody> | null {
  const err = error as { response?: { status?: number; data?: unknown } };
  if (err?.response?.status !== 401) {
    return null;
  }
  const data = err.response.data;
  return data && typeof data === 'object' ? (data as Partial<MFAChallengeBody>) : null;
}

/** Extract the challenge body (null when this is not a challenge). */
export function mfaChallengeOf(error: unknown): MFAChallengeBody | null {
  if (!isMfaChallenge(error)) {
    return null;
  }
  const data = mfaChallengeData(error) as MFAChallengeBody;
  return { ...data, enrollment_needed: Boolean(data.enrollment_needed) };
}

/** Answer a login challenge (TOTP code or recovery code). */
export async function verifyMfaChallenge(mfaToken: string, code: string): Promise<void> {
  await api.post('/auth/mfa/verify', { mfa_token: mfaToken, code });
}

/** Provisioning for the forced-enrollment login path. */
export async function enrollForcedMfa(mfaToken: string): Promise<MFAEnrollment> {
  const res = await api.post<MFAEnrollment>('/auth/mfa/enroll', { mfa_token: mfaToken });
  return res.data;
}

export async function getMyMfa(): Promise<MFAStatus> {
  const res = await api.get<MFAStatus>('/me/mfa');
  return res.data;
}

export async function enrollMyMfa(): Promise<MFAEnrollment> {
  const res = await api.post<MFAEnrollment>('/me/mfa/enroll');
  return res.data;
}

export async function confirmMyMfa(code: string): Promise<MFAStatus> {
  const res = await api.post<MFAStatus>('/me/mfa/confirm', { code });
  return res.data;
}

export async function disableMyMfa(password: string): Promise<MFAStatus> {
  const res = await api.request<MFAStatus>({
    method: 'DELETE',
    url: '/me/mfa',
    data: { password },
  });
  return res.data;
}

/** Admin force/release ("Require MFA") for a tenant member. */
export async function setUserMfaEnforced(
  tenantId: string,
  userId: string,
  enforced: boolean,
): Promise<void> {
  await api.patch(`/admin/tenants/${tenantId}/users/${userId}/mfa`, { enforced });
}
