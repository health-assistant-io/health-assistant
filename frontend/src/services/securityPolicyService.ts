/**
 * Tenant security policy API (ADR 0054 Phase 3 — 8C admin page contract).
 *
 * `GET/PATCH /tenants/{id}/security-policy`: read for staff
 * (`settings:read`), write for tenant admins (`settings:write` + the ADMIN
 * role pin). The response is the normalized policy — unset knobs answer
 * their defaults, unknown stored values never surface.
 */
import api from '../api/axios';

/** The normalized policy (`TenantSecurityPolicyResponse`). */
export interface TenantSecurityPolicy {
  tenant_id: string;
  /** MFA-required roles among ADMIN/MANAGER/USER (SYSTEM_ADMIN is a
   * constant always-on and can neither be listed nor waived). */
  mfa_required_roles: string[];
  /** Always `true` — informational constant. */
  system_admin_mfa_required: boolean;
  /** Nullable knobs — `null` = feature off (falls back to platform
   * defaults). */
  session_idle_minutes: number | null;
  session_max_hours: number | null;
  concurrent_session_limit: number | null;
  /** Canonical CIDR ranges (empty = allow any address). */
  ip_allowlist: string[];
  patient_sharing: boolean;
  manager_scope: 'tenant' | 'assigned';
  updated_at: string | null;
}

/** Partial patch — omitted fields are untouched, explicit `null` clears
 * the nullable knob. */
export interface TenantSecurityPolicyPatchPayload {
  mfa_required_roles?: string[] | null;
  session_idle_minutes?: number | null;
  session_max_hours?: number | null;
  concurrent_session_limit?: number | null;
  ip_allowlist?: string[] | null;
  patient_sharing?: boolean | null;
  manager_scope?: 'tenant' | 'assigned' | null;
}

/** The roles a policy may require MFA for (the `_POLICY_MFA_ROLES` closed
 * set; SYSTEM_ADMIN is never eligible). */
export const POLICY_MFA_ROLES = ['ADMIN', 'MANAGER', 'USER'] as const;

export async function getSecurityPolicy(tenantId: string): Promise<TenantSecurityPolicy> {
  const r = await api.get<TenantSecurityPolicy>(`/tenants/${tenantId}/security-policy`);
  return r.data;
}

export async function updateSecurityPolicy(
  tenantId: string,
  patch: TenantSecurityPolicyPatchPayload,
): Promise<TenantSecurityPolicy> {
  const r = await api.patch<TenantSecurityPolicy>(`/tenants/${tenantId}/security-policy`, patch);
  return r.data;
}
