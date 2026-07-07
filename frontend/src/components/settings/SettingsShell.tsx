import { NavLink, Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../../store/slices/authSlice';
import type { SettingsNavItem, SettingsNavHeader, SettingsRole } from '../../config/settingsNav';

interface SettingsShellProps {
  /** Sidebar entries (already scoped to this level). Role-gated items are filtered here. */
  nav: SettingsNavItem[];
  /** Optional level badge rendered above the nav (icon + scope title). */
  header?: SettingsNavHeader;
}

/**
 * Generic two-pane settings shell: sticky left nav + right content outlet.
 *
 * Level-agnostic — caller picks the nav preset (user / tenant / system) from
 * `config/settingsNav`. Adding a settings section is a one-line change to the
 * preset, not an edit to this component.
 */
function SettingsShell({ nav, header }: SettingsShellProps) {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const userRole = user?.role as SettingsRole | undefined;

  const visibleNav = userRole
    ? nav.filter((item) => !item.roles || item.roles.includes(userRole))
    : nav;

  const HeaderIcon = header?.icon;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
      <nav className="lg:col-span-1">
        <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-3 space-y-1 lg:sticky lg:top-24">
          {HeaderIcon && header && (
            <div className="flex items-center gap-2.5 px-3 py-2 mb-2 border-b border-gray-100 dark:border-dark-border">
              <HeaderIcon className="w-5 h-5 text-blue-600 dark:text-blue-400 shrink-0" />
              <span className="text-sm font-black text-gray-900 dark:text-dark-text truncate">
                {t(header.titleKey, header.titleFallback)}
              </span>
            </div>
          )}

          {visibleNav.map(({ to, icon: Icon, labelKey, labelFallback, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `w-full flex items-center px-3 py-2.5 text-sm rounded-xl transition-colors ${
                  isActive
                    ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 font-bold'
                    : 'text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg font-medium'
                }`
              }
            >
              <Icon className="w-4 h-4 mr-3 shrink-0" />
              <span className="truncate">{t(labelKey, labelFallback)}</span>
            </NavLink>
          ))}
        </div>
      </nav>

      <div className="lg:col-span-3">
        <Outlet />
      </div>
    </div>
  );
}

export default SettingsShell;
