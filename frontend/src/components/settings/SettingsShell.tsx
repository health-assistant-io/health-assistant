import { useLayoutEffect, useRef } from 'react';
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

  // Reset the scroll container on tab change so the sticky nav rail always
  // sits at the same offset — without this it drifts with stale scroll
  // positions from the previous (longer) tab.
  const rootRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    rootRef.current?.closest('main')?.scrollTo({ top: 0 });
  }, [location.pathname]);

  return (
    <div ref={rootRef} className="contents">
      <LibrarySettingsShell
        nav={items}
        active={active}
        onNavigate={(id) => navigate(id)}
        navClassName="lg:top-0 lg:max-h-[calc(100vh-3.5rem)] lg:overflow-y-auto"
        header={
          header
            ? { icon: header.icon, title: t(header.titleKey, header.titleFallback) }
            : undefined
        }
      >
        <Outlet />
      </LibrarySettingsShell>
    </div>
  );
}

export default SettingsShell;
