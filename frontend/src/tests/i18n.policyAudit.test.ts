/**
 * i18n key audit for the Phase 8C admin surfaces (tenant security policy +
 * admin audit viewer — ADR 0054 Phase 3 UI).
 *
 * Pins, in BOTH locales (en + el), identical key sets for:
 *   - `admin.policy.*`  — the tenant security-policy editor;
 *   - `admin.audit.*`   — the audit-stream viewer;
 *   - the nav labels `admin.security_policy` / `admin.audit_viewer`;
 *   - the auth keys for the new policy-denial login/session messages.
 * Also pins the reader-visible contracts: SYSTEM_ADMIN MFA is a constant
 * always-on, and the legal-holds surface answers "no holds".
 */
import { describe, it, expect } from 'vitest';
import en from '../locales/en.json';
import el from '../locales/el.json';

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

const EN_POLICY = keysOf(en as Node, 'admin.policy');
const EL_POLICY = keysOf(el as Node, 'admin.policy');
const EN_AUDIT = keysOf(en as Node, 'admin.audit');
const EL_AUDIT = keysOf(el as Node, 'admin.audit');

describe('i18n — phase 8C admin policy + audit keys (en = el)', () => {
  it('declares identical admin.policy key sets in both locales', () => {
    expect([...EL_POLICY].filter((key) => !EN_POLICY.has(key))).toEqual([]);
    expect([...EN_POLICY].filter((key) => !EL_POLICY.has(key))).toEqual([]);
    expect(EN_POLICY.size).toBeGreaterThan(20);
  });

  it('declares identical admin.audit key sets in both locales', () => {
    expect([...EL_AUDIT].filter((key) => !EN_AUDIT.has(key))).toEqual([]);
    expect([...EN_AUDIT].filter((key) => !EL_AUDIT.has(key))).toEqual([]);
    expect(EN_AUDIT.size).toBeGreaterThan(30);
  });

  it('pins the nav labels in both locales', () => {
    expect((en as Node).admin && (el as Node).admin).toBeTruthy();
    expect(keysOf(en as Node, 'admin').has('admin.security_policy')).toBe(true);
    expect(keysOf(el as Node, 'admin').has('admin.security_policy')).toBe(true);
    expect(keysOf(en as Node, 'admin').has('admin.audit_viewer')).toBe(true);
    expect(keysOf(el as Node, 'admin').has('admin.audit_viewer')).toBe(true);
  });

  it('pins the new auth denial messages in both locales', () => {
    const enAuth = (en as Node).auth as Node;
    const elAuth = (el as Node).auth as Node;
    for (const key of [
      'error_ip_not_allowed',
      'session_ended_idle',
      'session_ended_max_age',
      'session_ended_ip',
    ]) {
      expect(typeof enAuth[key]).toBe('string');
      expect(typeof elAuth[key]).toBe('string');
    }
  });

  it('keeps the SYSTEM_ADMIN always-on constant and the legal-holds note', () => {
    expect(((en as Node).admin as Node).policy && EN_POLICY.has('admin.policy.mfa_always_on')).toBe(true);
    expect(EN_AUDIT.has('admin.audit.legal_holds_null')).toBe(true);
    expect(EL_AUDIT.has('admin.audit.legal_holds_null')).toBe(true);
  });
});
