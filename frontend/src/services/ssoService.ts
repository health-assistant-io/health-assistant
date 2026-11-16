/**
 * Per-tenant OIDC SSO + SCIM admin surfaces (program 12B, ADR 0054 D2).
 *
 * Login-page discovery (`/auth/sso/status`) answers one bit — no tenant
 * enumeration. The browser flow opens the provider in a popup: the backend
 * callback sets the §10 cookie triple and either lands the app session or
 * answers the typed MFA-challenge JSON, which the opener reads (same
 * origin) and completes locally.
 */
import api from '../api/axios';

export interface SsoStatus {
  sso_available: boolean;
}

export interface SsoAuthorizeResult {
  authorization_url: string;
  issuer: string;
}

/** One bit: does this address's domain resolve an enabled SSO config? */
export async function checkSsoAvailability(email: string): Promise<boolean> {
  const response = await api.post<SsoStatus>('/auth/sso/status', { email });
  return Boolean(response.data?.sso_available);
}

export async function beginSso(
  target: { email?: string; tenant_slug?: string },
): Promise<SsoAuthorizeResult> {
  const response = await api.post<SsoAuthorizeResult>('/auth/sso/authorize', target);
  return response.data;
}

/** The authenticated link flow: a signed-in local user confirms the link. */
export async function confirmSsoLink(linkToken: string): Promise<void> {
  await api.post('/auth/sso/link', { link_token: linkToken });
}

export interface SsoCallbackOutcome {
  kind: 'mfa_required' | 'link_required' | 'error';
  mfaToken?: string;
  enrollmentNeeded?: boolean;
  methods?: string[];
  origin?: string;
  linkToken?: string;
  code?: string;
  message?: string;
}

/**
 * Read the backend callback document rendered inside the SSO popup.
 *
 * The popup is same-origin once the IdP redirects back, so the opener may
 * inspect it; while it is still on the provider's domain access throws
 * (cross-origin) and the caller keeps polling for the session instead.
 */
export function readSsoPopupOutcome(popup: Window): SsoCallbackOutcome | null {
  let href = '';
  let text = '';
  try {
    href = popup.location.href;
    text = popup.document.body?.innerText ?? '';
  } catch {
    return null;
  }
  if (!href.includes('/auth/sso/callback')) return null;
  const trimmed = text.trim();
  if (!trimmed.startsWith('{')) return null;
  let body: unknown;
  try {
    body = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  if (record.detail === 'mfa_required' && typeof record.mfa_token === 'string') {
    return {
      kind: 'mfa_required',
      mfaToken: record.mfa_token,
      enrollmentNeeded: Boolean(record.enrollment_needed),
      methods: Array.isArray(record.methods)
        ? record.methods.filter((method): method is string => typeof method === 'string')
        : [],
      origin: typeof record.origin === 'string' ? record.origin : undefined,
    };
  }
  const detail = record.detail;
  if (detail && typeof detail === 'object') {
    const typed = detail as Record<string, unknown>;
    if (typed.code === 'sso_link_required') {
      return {
        kind: 'link_required',
        linkToken: typeof typed.link_token === 'string' ? typed.link_token : undefined,
        code: 'sso_link_required',
        message: typeof typed.message === 'string' ? typed.message : undefined,
      };
    }
    return {
      kind: 'error',
      code: typeof typed.code === 'string' ? typed.code : undefined,
      message: typeof typed.message === 'string' ? typed.message : undefined,
    };
  }
  if (typeof detail === 'string') {
    return { kind: 'error', message: detail };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Admin — SSO configuration CRUD
// ---------------------------------------------------------------------------

export type SsoRole = 'ADMIN' | 'MANAGER' | 'USER';

export interface SsoConfig {
  id: string;
  tenant_id: string;
  issuer: string;
  client_id: string;
  client_secret_ref: string;
  allowed_domains: string[];
  default_role: SsoRole;
  group_role_map: Record<string, string>;
  enabled: boolean;
  jit_enabled: boolean;
  scopes: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface SsoConfigPayload {
  issuer: string;
  client_id: string;
  client_secret_ref: string;
  allowed_domains: string[];
  default_role?: SsoRole;
  group_role_map?: Record<string, string>;
  jit_enabled?: boolean;
  enabled?: boolean;
  scopes?: string[];
}

export type SsoConfigPatch = Partial<SsoConfigPayload>;

export interface SsoTestConnectionResult {
  secret_resolved: boolean;
  discovery_ok: boolean;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  error: string | null;
}

export async function listSsoConfigs(tenantId: string): Promise<SsoConfig[]> {
  const response = await api.get<{ items: SsoConfig[]; total: number }>(
    `/admin/tenants/${tenantId}/sso`,
  );
  return Array.isArray(response.data?.items) ? response.data.items : [];
}

export async function createSsoConfig(
  tenantId: string,
  payload: SsoConfigPayload,
): Promise<SsoConfig> {
  const response = await api.post<SsoConfig>(`/admin/tenants/${tenantId}/sso`, payload);
  return response.data;
}

export async function updateSsoConfig(
  tenantId: string,
  configId: string,
  patch: SsoConfigPatch,
): Promise<SsoConfig> {
  const response = await api.patch<SsoConfig>(
    `/admin/tenants/${tenantId}/sso/${configId}`,
    patch,
  );
  return response.data;
}

export async function deleteSsoConfig(tenantId: string, configId: string): Promise<void> {
  await api.delete(`/admin/tenants/${tenantId}/sso/${configId}`);
}

export async function testSsoConnection(
  tenantId: string,
  configId: string,
): Promise<SsoTestConnectionResult> {
  const response = await api.post<SsoTestConnectionResult>(
    `/admin/tenants/${tenantId}/sso/${configId}/test-connection`,
  );
  return response.data;
}

// ---------------------------------------------------------------------------
// Admin — SCIM tokens
// ---------------------------------------------------------------------------

export interface ScimToken {
  id: string;
  name: string;
  scopes: string[];
  active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string | null;
}

export interface ScimTokenCreated extends ScimToken {
  token: string;
}

export async function listScimTokens(tenantId: string): Promise<ScimToken[]> {
  const response = await api.get<{ items: ScimToken[]; total: number }>(
    `/admin/tenants/${tenantId}/scim-tokens`,
  );
  return Array.isArray(response.data?.items) ? response.data.items : [];
}

export async function createScimToken(
  tenantId: string,
  payload: { name: string; expires_at?: string | null },
): Promise<ScimTokenCreated> {
  const response = await api.post<ScimTokenCreated>(
    `/admin/tenants/${tenantId}/scim-tokens`,
    payload,
  );
  return response.data;
}

export async function revokeScimToken(tenantId: string, tokenId: string): Promise<void> {
  await api.delete(`/admin/tenants/${tenantId}/scim-tokens/${tokenId}`);
}
