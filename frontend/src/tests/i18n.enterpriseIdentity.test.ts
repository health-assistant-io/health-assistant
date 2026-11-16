/**
 * i18n key audit for the Phase 12C3 enterprise identity UI.
 *
 * Pins identical key sets in both locales for every new surface:
 *   - `auth.*` SSO / passkey / MFA-method copy;
 *   - `settings.*` passkeys, session devices and API tokens;
 *   - `step_up.*` the global re-authentication prompt;
 *   - `enterprise.*` SSO + SCIM admin pages;
 *   - `elevations.*` the break-glass queue and the caller's own ledger.
 */
import { describe, expect, it } from 'vitest';
import en from '../locales/en/common.json';
import el from '../locales/el/common.json';

type Node = Record<string, unknown>;

const flatten = (value: unknown, prefix = '', out: Set<string> = new Set()): Set<string> => {
  if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value as Node)) {
      flatten(child, prefix ? `${prefix}.${key}` : key, out);
    }
  } else {
    out.add(prefix);
  }
  return out;
};

const keysOf = (root: Node, path: string): Set<string> => {
  const node = path.split('.').reduce<unknown>((acc, key) => {
    if (acc && typeof acc === 'object') return (acc as Node)[key];
    return undefined;
  }, root);
  return flatten(node, path);
};

function expectParity(path: string, minimum: number) {
  const enKeys = keysOf(en as Node, path);
  const elKeys = keysOf(el as Node, path);
  expect([...elKeys].filter((key) => !enKeys.has(key))).toEqual([]);
  expect([...enKeys].filter((key) => !elKeys.has(key))).toEqual([]);
  expect(enKeys.size).toBeGreaterThanOrEqual(minimum);
}

describe('i18n — Phase 12C3 enterprise identity (en = el)', () => {
  it('declares identical step-up keys', () => {
    expectParity('step_up', 18);
    const keys = keysOf(en as Node, 'step_up');
    for (const action of [
      'role_change',
      'export',
      'break_glass',
      'grant_admin',
      'mfa_change',
    ]) {
      expect(keys.has(`step_up.action_${action}`)).toBe(true);
    }
  });

  it('declares identical enterprise SSO + SCIM keys', () => {
    expectParity('enterprise', 60);
    expectParity('enterprise.sso', 30);
    expectParity('enterprise.scim', 15);
    expect(keysOf(en as Node, 'enterprise').has('enterprise.nav_identity')).toBe(true);
  });

  it('declares identical elevation keys', () => {
    expectParity('elevations', 35);
    const keys = keysOf(en as Node, 'elevations');
    for (const status of [
      'pending',
      'active',
      'expired',
      'revoked',
      'denied',
      'cancelled',
    ]) {
      expect(keys.has(`elevations.status_${status}`)).toBe(true);
    }
    expect(keys.has('elevations.nav_tenant')).toBe(true);
    expect(keys.has('elevations.nav_system')).toBe(true);
  });

  it('pins the login SSO/passkey keys in auth', () => {
    const enAuth = (en as Node).auth as Node;
    const elAuth = (el as Node).auth as Node;
    for (const key of [
      'sso_sign_in',
      'sso_link',
      'sso_email_required',
      'sso_unavailable',
      'sso_link_required',
      'sso_popup_blocked',
      'passkey_sign_in',
      'passkey_failed',
      'mfa_passkey_hint',
      'mfa_use_passkey',
      'mfa_no_factor',
    ]) {
      expect(typeof enAuth[key]).toBe('string');
      expect(typeof elAuth[key]).toBe('string');
    }
  });

  it('pins the passkey, session-device and API-token settings keys', () => {
    const enSettings = (en as Node).settings as Node;
    const elSettings = (el as Node).settings as Node;
    for (const key of [
      'passkeys_title',
      'passkeys_add',
      'passkeys_delete_title',
      'passkeys_uv_note',
      'sessions_last_seen',
      'sessions_revoke_others',
      'api_tokens_title',
      'api_tokens_secret_warning',
      'api_tokens_patient_scope_label',
      'api_tokens_rotate',
      'api_tokens_revoke',
      'recovery_codes_note',
    ]) {
      expect(typeof enSettings[key]).toBe('string');
      expect(typeof elSettings[key]).toBe('string');
    }
  });

  it('pins the tenant detail identity tab label', () => {
    const enTenants = ((en as Node).admin as Node).tenants as Node;
    const elTenants = ((el as Node).admin as Node).tenants as Node;
    expect(enTenants.tab_identity).toBe('Enterprise identity');
    expect(typeof elTenants.tab_identity).toBe('string');
  });
});
