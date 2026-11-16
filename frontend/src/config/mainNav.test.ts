import { describe, expect, it } from 'vitest';
import {
  MAIN_NAV,
  filterNavByRole,
  resolveActiveNavId,
  resolveSubPath,
} from './mainNav';

describe('resolveSubPath', () => {
  it('resolves the patient-detail token against the current patient', () => {
    expect(resolveSubPath('/patient-info', 42)).toBe('/patients/42');
    expect(resolveSubPath('/patient-info')).toBe('/patients');
    expect(resolveSubPath('/examinations', 42)).toBe('/examinations');
  });
});

describe('filterNavByRole', () => {
  it('keeps ungated items for an unauthenticated user', () => {
    const filtered = filterNavByRole(MAIN_NAV);
    expect(filtered.map((i) => i.path)).toEqual(MAIN_NAV.filter((i) => !i.roles).map((i) => i.path));
  });

  it('includes the Administration group only for allowed roles', () => {
    expect(filterNavByRole(MAIN_NAV, 'USER').some((i) => i.path === '/administration')).toBe(false);
    expect(filterNavByRole(MAIN_NAV, 'MANAGER').some((i) => i.path === '/administration')).toBe(true);
  });

  it('filters sub-items by role inside a group', () => {
    const admin = filterNavByRole(MAIN_NAV, 'ADMIN').find((i) => i.path === '/administration');
    const paths = admin?.subItems?.map((s) => s.path) ?? [];
    expect(paths).toContain('/admin/tenant/users');
    expect(paths).not.toContain('/admin/system/tenants'); // SYSTEM_ADMIN only
    expect(paths).toContain('/patients');
  });

  it('keeps the section divider only on the first surviving item', () => {
    const admin = filterNavByRole(MAIN_NAV, 'ADMIN').find((i) => i.path === '/administration');
    const sections = admin?.subItems?.filter((s) => s.section).map((s) => s.section);
    expect(sections).toEqual(['admin.tenant_management']);
  });

  it('drops a group entirely when role filtering empties it', () => {
    // SYSTEM_ADMIN-only group remains visible for SYSTEM_ADMIN, gone for USER
    const forUser = filterNavByRole(MAIN_NAV, 'USER');
    expect(forUser.some((i) => i.path === '/administration')).toBe(false);
  });

  // --- permission gating (the /me/access mirror, ADR 0054) ---

  const can = (permission: string) =>
    ({
      user: false,
      'user:read': false,
      'user:invite': true,
      'tenant:read': true,
      'tenant:manage': false,
      'settings:read': true,
      'integration:manage': false,
      'patient:read': true,
      'clinical:read': true,
      'catalog:read': true,
    })[permission] ?? false;

  it('hides permission-gated sub-items when the caller holds none of them', () => {
    // A MANAGER-shaped `can`: holds settings/patient/clinical reads, no
    // user:read, no integration:manage.
    const manager = filterNavByRole(MAIN_NAV, 'MANAGER', can).find(
      (i) => i.path === '/administration',
    );
    const paths = manager?.subItems?.map((s) => s.path) ?? [];
    // The matrix outcomes: MANAGER loses the user directory and the
    // OAuth-client management; the tenant settings stay.
    expect(paths).not.toContain('/admin/tenant/users');
    expect(paths).not.toContain('/admin/tenant/oauth-clients');
    expect(paths).toContain('/admin/tenant/settings');
    expect(paths).not.toContain('/patients'); // roles gate unchanged
  });

  it('keeps ADMIN surfaces when the caller holds the permissions', () => {
    const adminCan = (permission: string) =>
      ['user:read', 'integration:manage', 'settings:read', 'patient:read', 'clinical:read'].includes(
        permission,
      );
    const admin = filterNavByRole(MAIN_NAV, 'ADMIN', adminCan).find(
      (i) => i.path === '/administration',
    );
    const paths = admin?.subItems?.map((s) => s.path) ?? [];
    expect(paths).toContain('/admin/tenant/users');
    expect(paths).toContain('/admin/tenant/oauth-clients');
    expect(paths).toContain('/admin/tenant/settings');
    expect(paths).toContain('/patients');
  });

  it('exposes the Access management leaf behind patient:grant / settings:read', () => {
    const bothCan = (permission: string) =>
      ['patient:grant', 'settings:read'].includes(permission);
    const withBoth = filterNavByRole(MAIN_NAV, 'MANAGER', bothCan)
      .find((i) => i.path === '/administration');
    expect(withBoth?.subItems?.map((s) => s.path)).toContain('/admin/tenant/access');

    const neitherCan = () => false;
    const without = filterNavByRole(MAIN_NAV, 'MANAGER', neitherCan)
      .find((i) => i.path === '/administration');
    // Every leaf gated off drops the whole group (existing contract).
    expect(without).toBeUndefined();

    // Roles gate: USER never sees the admin surface even with patient:grant.
    const userCan = () => true;
    expect(
      filterNavByRole(MAIN_NAV, 'USER', userCan).some((i) => i.path === '/administration'),
    ).toBe(false);
  });

  it('ignores permission gating when no can predicate is supplied', () => {
    // Roles-only behavior (the old contract) must stay intact for callers
    // that have not hydrated the mirror yet.
    const manager = filterNavByRole(MAIN_NAV, 'MANAGER').find(
      (i) => i.path === '/administration',
    );
    expect(manager?.subItems?.map((s) => s.path)).toContain('/admin/tenant/oauth-clients');
  });

  it('hides the whole group when permission filtering empties it', () => {
    const nothingCan = () => false;
    expect(
      filterNavByRole(MAIN_NAV, 'ADMIN', nothingCan).some((i) => i.path === '/administration'),
    ).toBe(false);
  });
});

describe('resolveActiveNavId', () => {
  it('treats / and /dashboard as the dashboard', () => {
    expect(resolveActiveNavId('/', '')).toBe('/dashboard');
    expect(resolveActiveNavId('/dashboard', '')).toBe('/dashboard');
  });

  it('matches leaves by exact path or trailing prefix', () => {
    expect(resolveActiveNavId('/catalogs', '')).toBe('/catalogs');
    expect(resolveActiveNavId('/notifications/xyz', '')).toBe('/notifications');
    expect(resolveActiveNavId('/ai-assistant', '')).toBe('/ai-assistant');
  });

  it('resolves the patient-detail overview for any /patients/{id} page', () => {
    expect(resolveActiveNavId('/patients/42', '', 42)).toBe('/patients/42');
    expect(resolveActiveNavId('/patients/42/edit', '', 42)).toBe('/patients/42');
  });

  it('matches the patient list exactly, not as a prefix (role-gated group)', () => {
    expect(resolveActiveNavId('/patients', '', undefined, 'ADMIN')).toBe('/patients');
    expect(resolveActiveNavId('/patients/42', '', 42, 'ADMIN')).toBe('/patients/42');
  });

  it('matches the catalogs workspace leaf regardless of type param', () => {
    expect(resolveActiveNavId('/catalogs', '')).toBe('/catalogs');
    expect(resolveActiveNavId('/catalogs', '?type=biomarker')).toBe('/catalogs');
  });

  it('matches admin routes only for the right role', () => {
    expect(resolveActiveNavId('/admin/system/tenants', '', undefined, 'SYSTEM_ADMIN')).toBe(
      '/admin/system/tenants',
    );
    expect(resolveActiveNavId('/admin/system/tenants', '', undefined, 'USER')).toBeNull();
  });

  it('returns null for unknown paths', () => {
    expect(resolveActiveNavId('/nowhere', '')).toBeNull();
  });
});
