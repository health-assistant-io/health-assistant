import type { LucideIcon } from 'lucide-react';
import {
  UserCircle,
  SlidersHorizontal,
  Shield,
  Palette,
  Cpu,
  Plug,
  Bell,
  Download,
  Building2,
  Globe,
  Database,
  Bone,
} from 'lucide-react';

/**
 * Role values as used at runtime (uppercase — see authSlice). Kept as a local
 * string union so this registry has no dependency on the (inconsistent) role
 * type definitions scattered across the codebase.
 */
export type SettingsRole = 'SYSTEM_ADMIN' | 'ADMIN' | 'MANAGER' | 'USER';

/**
 * A single entry in a settings sidebar. Pure data — the shell renders it.
 *
 * - `labelKey`     i18n key (with `labelFallback` if missing).
 * - `roles`        optional gate; item is hidden for users whose role isn't listed.
 * - `end`          forwarded to NavLink — set on an index/landing route so it
 *                  doesn't stay "active" when a sibling is selected.
 */
export interface SettingsNavItem {
  to: string;
  icon: LucideIcon;
  labelKey: string;
  labelFallback: string;
  roles?: SettingsRole[];
  end?: boolean;
}

/**
 * Optional header rendered above the nav list — typically a level badge
 * (icon + title) that tells the user which scope they're editing.
 */
export interface SettingsNavHeader {
  icon: LucideIcon;
  titleKey: string;
  titleFallback: string;
}

/**
 * User-level ("My Settings"). Reaches across to /profile and /settings/*.
 * The Export & Import entry is gated to admins.
 */
export const userSettingsNav: SettingsNavItem[] = [
  { to: '/profile', icon: UserCircle, labelKey: 'settings.nav_profile', labelFallback: 'Profile', end: true },
  { to: '/settings/preferences', icon: SlidersHorizontal, labelKey: 'settings.nav_preferences', labelFallback: 'Preferences' },
  { to: '/settings/security', icon: Shield, labelKey: 'settings.nav_security', labelFallback: 'Security' },
  { to: '/settings/appearance', icon: Palette, labelKey: 'settings.appearance_short', labelFallback: 'Appearance & Visualization' },
  { to: '/settings/ai-config', icon: Cpu, labelKey: 'settings.nav_ai', labelFallback: 'AI Configuration' },
  { to: '/settings/integrations', icon: Plug, labelKey: 'common.integrations', labelFallback: 'Integrations' },
  { to: '/settings/notifications', icon: Bell, labelKey: 'settings.nav_notifications', labelFallback: 'Notifications' },
  { to: '/settings/export-import', icon: Download, labelKey: 'backup.title', labelFallback: 'Export & Import', roles: ['ADMIN', 'SYSTEM_ADMIN'] },
];

export const userSettingsHeader: SettingsNavHeader = {
  icon: UserCircle,
  titleKey: 'settings.user_header',
  titleFallback: 'My Settings',
};

/**
 * Tenant-level ("Tenant Settings"). General tiered defaults + AI config.
 *
 * Note: there is no tenant-scoped integrations admin page today — integrations
 * are enabled per-patient at user level and globally toggled at system level.
 * When a tenant integrations view is added, append one entry here.
 */
export const tenantSettingsNav: SettingsNavItem[] = [
  { to: '/admin/tenant/settings', icon: Building2, labelKey: 'settings.nav_general', labelFallback: 'General', end: true },
  { to: '/admin/tenant/ai-config', icon: Cpu, labelKey: 'settings.nav_ai', labelFallback: 'AI Configuration' },
];

export const tenantSettingsHeader: SettingsNavHeader = {
  icon: Building2,
  titleKey: 'settings.tenant_title',
  titleFallback: 'Tenant Settings',
};

/**
 * System-level ("System Settings"). General tiered defaults + the global
 * configuration surfaces (AI, integrations, ontology, taxonomy, atlas).
 */
export const systemSettingsNav: SettingsNavItem[] = [
  { to: '/admin/system/settings', icon: Globe, labelKey: 'settings.nav_general', labelFallback: 'General', end: true },
  { to: '/admin/system/ai-config', icon: Cpu, labelKey: 'settings.nav_ai', labelFallback: 'AI Configuration' },
  { to: '/admin/system/integrations', icon: Plug, labelKey: 'common.integrations', labelFallback: 'Integrations' },
  // Catalogs moved to top-level nav (/catalogs, all users) — Phase C.
  { to: '/admin/anatomy-atlas', icon: Bone, labelKey: 'settings.nav_anatomy_atlas', labelFallback: 'Anatomy Atlas' },
];

export const systemSettingsHeader: SettingsNavHeader = {
  icon: Globe,
  titleKey: 'settings.system_title',
  titleFallback: 'System Settings',
};
