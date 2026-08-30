import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../../store/slices/authSlice';
import type { SettingsNavItem, SettingsNavHeader, SettingsRole } from '../../config/settingsNav';
import {
  SettingsShell as LibrarySettingsShell,
  type SettingsNavItem as LibraryNavItem,
} from '@neuronection/assistant-ui';

interface SettingsShellProps {
  /** Sidebar entries (already scoped to this level). Role-gated items are filtered here. */
  nav: SettingsNavItem[];
  /** Optional level badge rendered above the nav (icon + scope title). */
  header?: SettingsNavHeader;
}

/**
 * Route glue around the library `SettingsShell`: maps the app's nav preset
 * (paths + i18n keys + role gating) onto the controlled library component
 * and renders the matched child route in the content pane.
 */
function SettingsShell({ nav, header }: SettingsShellProps) {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const userRole = user?.role as SettingsRole | undefined;
  const location = useLocation();
  const navigate = useNavigate();

  const items: LibraryNavItem[] = nav
    .filter((item) => !userRole || !item.roles || item.roles.includes(userRole))
    .map(({ to, icon, labelKey, labelFallback }) => ({
      id: to,
      icon,
      label: t(labelKey, labelFallback),
    }));

  // Longest matching prefix wins so nested sections highlight correctly.
  const active = items
    .filter((item) => location.pathname.startsWith(item.id))
    .sort((a, b) => b.id.length - a.id.length)[0]?.id;

  return (
    <LibrarySettingsShell
      nav={items}
      active={active}
      onNavigate={(id) => navigate(id)}
      header={
        header
          ? { icon: header.icon, title: t(header.titleKey, header.titleFallback) }
          : undefined
      }
    >
      <Outlet />
    </LibrarySettingsShell>
  );
}

export default SettingsShell;
