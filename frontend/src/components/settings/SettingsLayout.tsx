import { NavLink, Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Cpu, Plug, Palette, Bell, Download, UserCircle, SlidersHorizontal, Shield } from 'lucide-react';
import { useAuthStore } from '../../store/slices/authSlice';

function SettingsLayout() {
  const { t } = useTranslation();
  const { user } = useAuthStore();

  const sections = [
    { to: '/profile', icon: UserCircle, label: t('settings.nav_profile', 'Profile') },
    { to: '/settings/preferences', icon: SlidersHorizontal, label: t('settings.nav_preferences', 'Preferences') },
    { to: '/settings/security', icon: Shield, label: t('settings.nav_security', 'Security') },
    { to: '/settings/appearance', icon: Palette, label: t('settings.appearance_short', 'Appearance & Visualization') },
    { to: '/settings/ai-config', icon: Cpu, label: t('settings.nav_ai', 'AI Configuration') },
    { to: '/settings/integrations', icon: Plug, label: t('common.integrations', 'Integrations') },
    { to: '/settings/notifications', icon: Bell, label: t('settings.nav_notifications', 'Notifications') },
  ];

  if (user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN') {
    sections.push({ to: '/settings/export-import', icon: Download, label: t('backup.title', 'Export & Import') });
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
      <nav className="lg:col-span-1">
        <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-3 space-y-1 lg:sticky lg:top-24">
          {sections.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `w-full flex items-center px-3 py-2.5 text-sm rounded-xl transition-colors ${
                  isActive
                    ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 font-bold'
                    : 'text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg font-medium'
                }`
              }
            >
              <Icon className="w-4 h-4 mr-3 shrink-0" />
              <span className="truncate">{label}</span>
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

export default SettingsLayout;
