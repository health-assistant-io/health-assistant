import type { LucideIcon } from 'lucide-react';
import {
  Bell,
  BookOpen,
  Info,
  LayoutDashboard,
  ShieldCheck,
  Sparkles,
  User,
} from 'lucide-react';

export interface MainNavSubItem {
  path: string;
  labelKey: string;
  roles?: string[];
  /** When set, renders a section divider above this sub-item. */
  section?: string;
  /** Resolved dynamically — e.g. /patient-info becomes /patients/{id} or /patients. */
  dynamicPath?: 'patient-detail';
}

export interface MainNavItem {
  path: string;
  labelKey: string;
  icon: LucideIcon;
  subItems?: MainNavSubItem[];
  requiresPatient?: boolean;
  roles?: string[];
}

/** Primary sidebar registry — single source for the app shell. */
export const MAIN_NAV: MainNavItem[] = [
  // 1. Dashboard
  { path: '/dashboard', labelKey: 'common.dashboard', icon: LayoutDashboard },

  // 2. Patient Record (grouped — clinical record, treatments & alerts, timeline)
  {
    path: '/patient-record',
    labelKey: 'common.patient_record',
    icon: User,
    subItems: [
      { path: '/patient-info', labelKey: 'common.patient_overview', dynamicPath: 'patient-detail' },
      // ── Clinical Record ──
      { path: '/examinations', labelKey: 'common.examinations', section: 'common.section_clinical_record' },
      { path: '/documents', labelKey: 'common.documents_explorer' },
      { path: '/biomarkers', labelKey: 'common.biomarkers' },
      { path: '/analytics/correlative', labelKey: 'common.correlative_analytics' },
      // ── Treatments & Alerts ──
      { path: '/medications', labelKey: 'common.medications', section: 'common.section_treatments_alerts' },
      { path: '/vaccinations', labelKey: 'common.vaccinations' },
      { path: '/allergies', labelKey: 'common.allergies' },
      // ── Timeline ──
      { path: '/events', labelKey: 'events.title', section: 'common.section_timeline' },
      { path: '/calendar', labelKey: 'common.calendar' },
    ],
  },

  // 3. Notifications (app-level, not patient-scoped)
  { path: '/notifications', labelKey: 'common.notifications', icon: Bell },

  // 4. Catalogs (reference catalogs — all users). Single link to the unified
  //    tabbed workspace at /catalogs (formerly expanded into 6 ?type= shortcuts).
  { path: '/catalogs', labelKey: 'common.catalogs', icon: BookOpen },

  // 5. AI Assistant
  { path: '/ai-assistant', labelKey: 'common.ai_assistant', icon: Sparkles },

  // 6. About
  { path: '/about', labelKey: 'common.about', icon: Info },

  // 7. Administration (merged System + Tenant, role-gated)
  {
    path: '/administration',
    labelKey: 'common.administration',
    icon: ShieldCheck,
    roles: ['SYSTEM_ADMIN', 'ADMIN', 'MANAGER'],
    subItems: [
      // ── System section ──
      { path: '/admin/system/tenants', labelKey: 'admin.system_tenants', section: 'admin.system_administration', roles: ['SYSTEM_ADMIN'] },
      { path: '/admin/system/users', labelKey: 'admin.users', roles: ['SYSTEM_ADMIN'] },
      { path: '/admin/system/settings', labelKey: 'admin.system_settings', roles: ['SYSTEM_ADMIN'] },
      // ── Tenant section ──
      { path: '/admin/tenant/users', labelKey: 'admin.users', section: 'admin.tenant_management', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
      { path: '/admin/tenant/settings', labelKey: 'admin.tenant_settings', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
      { path: '/admin/tenant/oauth-clients', labelKey: 'admin.api_clients', roles: ['SYSTEM_ADMIN', 'ADMIN', 'MANAGER'] },
      { path: '/patients', labelKey: 'common.patients', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
      { path: '/doctors', labelKey: 'common.doctors', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
      { path: '/organizations', labelKey: 'common.organizations', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
    ],
  },
];

/** Resolve a sub-item path (handles the /patient-info dynamic token). */
export function resolveSubPath(path: string, currentPatientId?: string | number): string {
  if (path === '/patient-info') {
    return currentPatientId != null ? `/patients/${currentPatientId}` : '/patients';
  }
  return path;
}

/**
 * Role-filter the registry, preserving section dividers on the first
 * surviving item of each section and dropping groups whose items are all
 * filtered out. undefined role = unauthenticated = only ungated items.
 */
export function filterNavByRole(items: MainNavItem[], role?: string): MainNavItem[] {
  const allowed = (itemRoles?: string[]) =>
    !itemRoles || (role != null && itemRoles.includes(role));

  return items
    .filter((item) => allowed(item.roles))
    .map((item) => {
      if (!item.subItems) return item;
      const filteredSubItems = item.subItems.filter((sub) => allowed(sub.roles));
      // Preserve the section divider on the first surviving item of each section
      const seenSections = new Set<string>();
      const recomputed = filteredSubItems.map((sub) => {
        if (sub.section && seenSections.has(sub.section)) {
          const { section: _section, ...rest } = sub;
          return rest as MainNavSubItem;
        }
        if (sub.section) seenSections.add(sub.section);
        return sub;
      });
      if (recomputed.length === 0) return item; // group dropped below
      return { ...item, subItems: recomputed };
    })
    .filter((item) => {
      // Hide a role-gated group entirely if no sub-items survived filtering
      if (item.roles && item.subItems !== undefined && item.subItems.length === 0) return false;
      return true;
    });
}

/**
 * Resolve the active nav id from the location — the exact matching rules
 * the sidebar has always used, extracted so they are testable:
 * - patient-detail sub-item is active on any /patients/{id} page
 * - the patient list matches exactly (so it does not shadow the detail page)
 * - sub-items with ?type= query strings match pathname + the type param
 * - everything else matches by exact path or trailing-slash prefix
 */
export function resolveActiveNavId(
  pathname: string,
  search: string,
  currentPatientId?: string | number,
  role?: string,
): string | null {
  const isSubActive = (sub: MainNavSubItem): boolean => {
    if (sub.dynamicPath === 'patient-detail') {
      return /^\/patients\/[^/]+/.test(pathname);
    }
    if (sub.path === '/patients') {
      return pathname === '/patients';
    }
    if (sub.path.includes('?')) {
      const [base, query] = sub.path.split('?');
      const expected = new URLSearchParams(query).get('type');
      const actual = new URLSearchParams(search).get('type');
      return pathname === base && (!expected || actual === expected);
    }
    return pathname === sub.path || pathname.startsWith(sub.path + '/');
  };

  for (const item of filterNavByRole(MAIN_NAV, role)) {
    if (item.subItems) {
      const active = item.subItems.find(isSubActive);
      if (active) return resolveSubPath(active.path, currentPatientId);
      continue;
    }
    if (item.path === '/dashboard') {
      if (pathname === '/dashboard' || pathname === '/') return item.path;
      continue;
    }
    if (pathname === item.path || pathname.startsWith(item.path + '/')) return item.path;
  }
  return null;
}
