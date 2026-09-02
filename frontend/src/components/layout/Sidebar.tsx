import { Link, useLocation, useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useMemo } from 'react';
import { SidebarNav, type NavItem } from '@neuronection/assistant-ui';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useIsTablet } from '../../hooks/useMediaQuery';
import CreateMenu from '../ui/CreateMenu';
import { Breadcrumbs } from '../ui/Breadcrumbs';
import { SidebarFooter } from './SidebarFooter';
import { MAIN_NAV, filterNavByRole, resolveActiveNavId, resolveSubPath } from '../../config/mainNav';

function Sidebar() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const setSidebarOpen = useUIStore(state => state.setSidebarOpen);
  const sidebarOpen = useUIStore(state => state.sidebarOpen);
  const sidebarCollapsed = useUIStore(state => state.sidebarCollapsed);
  // On mobile/tablet (< lg) the sidebar is always fully expanded — the
  // collapsed icon-only mode is a desktop space-saving feature only.
  const isMobileView = useIsTablet();
  const effectiveCollapsed = sidebarCollapsed && !isMobileView;
  const toggleSidebarCollapse = useUIStore(state => state.toggleSidebarCollapse);
  const { currentPatient } = usePatientStore();
  const user = useAuthStore(state => state.user);
  const theme = useSettingsStore(state => state.theme);
  const pageHeaderConfig = useUIStore(state => state.pageHeaderConfig);

  const items = useMemo<NavItem[]>(
    () =>
      filterNavByRole(MAIN_NAV, user?.role).map((item) => ({
        id: item.path,
        label: t(item.labelKey),
        icon: item.icon,
        children: item.subItems?.map((sub) => ({
          id: resolveSubPath(sub.path, currentPatient?.id),
          label: t(sub.labelKey),
          section: sub.section ? t(sub.section) : undefined,
        })),
      })),
    [t, user?.role, currentPatient?.id],
  );

  const activeId = resolveActiveNavId(
    location.pathname,
    location.search,
    currentPatient?.id,
    user?.role,
  );

  return (
    <SidebarNav
      items={items}
      activeId={activeId}
      onNavigate={(id) => navigate(id)}
      collapsed={effectiveCollapsed}
      onCollapsedChange={() => toggleSidebarCollapse()}
      collapsible
      labels={{
        navAria: t('nav.primary', 'Main navigation'),
        expand: t('nav.expand_sidebar', 'Expand Sidebar'),
        collapse: t('nav.collapse_sidebar', 'Collapse Sidebar'),
      }}
      className={`${effectiveCollapsed ? 'w-20' : 'w-64 sm:w-72 lg:w-64'} bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border shadow-lg lg:shadow-none safe-top safe-bottom isolate`}
      header={
        <>
          {/* Mobile close button — centered on the outer right edge */}
          {sidebarOpen && !effectiveCollapsed && (
            <button
              onClick={() => setSidebarOpen(false)}
              aria-label={t('nav.close_sidebar', 'Close sidebar')}
              className="lg:hidden absolute top-1/2 -translate-y-1/2 -right-4 z-50 w-8 h-8 flex items-center justify-center rounded-lg bg-white dark:bg-dark-surface ring-1 ring-black/5 dark:ring-white/10 shadow-md text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:shadow-lg hover:scale-105 active:scale-95 transition-all duration-150"
            >
              <X className="w-5 h-5" />
            </button>
          )}

          <div className={`p-6 flex items-center ${effectiveCollapsed ? 'justify-center' : 'justify-start'} mt-2 mb-4`}>
            <Link to="/" className="flex items-center space-x-3 hover:opacity-80 transition-opacity">
              <img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-9 h-9 shrink-0" alt="Health Assistant Logo" />
              {!effectiveCollapsed && <h1 className="text-xl font-bold text-brand-navy dark:text-white truncate">Health Assistant</h1>}
            </Link>
          </div>

          {/* Current page breadcrumb path — mobile/tablet only. On desktop (lg+)
              the path renders in the header bar instead. */}
          {!effectiveCollapsed && pageHeaderConfig?.breadcrumbs && pageHeaderConfig.breadcrumbs.length > 0 && (
            <div className="lg:hidden px-6 pb-3 -mt-1 mb-1 border-b border-gray-50 dark:border-white/5">
              <Breadcrumbs
                items={pageHeaderConfig.breadcrumbs}
                currentLabel={pageHeaderConfig.title}
              />
            </div>
          )}
        </>
      }
      footer={
        <div className="space-y-2">
          <CreateMenu collapsed={effectiveCollapsed} />
          {!effectiveCollapsed && (
            <div
              aria-hidden
              className="mt-2 h-px bg-gradient-to-r from-transparent via-gray-200 to-transparent dark:via-dark-border"
            />
          )}
          <div className={effectiveCollapsed ? undefined : 'pt-3'}>
            <SidebarFooter collapsed={effectiveCollapsed} />
          </div>
        </div>
      }
    />
  );
}

export default Sidebar;
