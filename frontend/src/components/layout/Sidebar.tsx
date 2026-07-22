import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  X,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  User,
  Bell,
  ShieldCheck,
  BookOpen
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useIsTablet } from '../../hooks/useMediaQuery';
import { useState, useMemo, useRef, Fragment, useEffect } from 'react';
import AppVersion from '../ui/AppVersion';
import CreateMenu from '../ui/CreateMenu';

interface SubItem {
  path: string;
  labelKey: string;
  roles?: string[];
  /** When set, renders a section divider above this sub-item (e.g. merged Administration group). */
  section?: string;
  /** Resolved dynamically — e.g. /patient-info becomes /patients/{id} or /patients. */
  dynamicPath?: 'patient-detail';
}

interface MenuItem {
  path: string;
  labelKey: string;
  icon: any;
  subItems?: SubItem[];
  requiresPatient?: boolean;
  roles?: string[];
}

function Sidebar() {
  const { t } = useTranslation();
  const location = useLocation();
  const setSidebarOpen = useUIStore(state => state.setSidebarOpen);
  const sidebarOpen = useUIStore(state => state.sidebarOpen);
  const sidebarCollapsed = useUIStore(state => state.sidebarCollapsed);
  // On mobile/tablet (< lg) the sidebar is always fully expanded — the
  // collapsed icon-only mode is a desktop space-saving feature only.
  // The persisted `sidebarCollapsed` store value is the desktop preference
  // and is preserved across navigation; we just ignore it below lg.
  const isMobileView = useIsTablet();
  const effectiveCollapsed = sidebarCollapsed && !isMobileView;
  const toggleSidebarCollapse = useUIStore(state => state.toggleSidebarCollapse);
  const { currentPatient } = usePatientStore();
  const user = useAuthStore(state => state.user);
  const theme = useSettingsStore(state => state.theme);
  const [expandedItems, setExpandedItems] = useState<string[]>(['/patient-record']);
  const [hoveredMenu, setHoveredMenu] = useState<{ path: string; rect: DOMRect, items?: SubItem[], labelKey: string } | null>(null);
  const [isHovered, setIsHovered] = useState(false);
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Collapse all expanded submenus when the mobile sidebar closes so they
  // don't linger expanded on reopen. Desktop (lg+) keeps the sidebar
  // permanently visible, so its expand state is preserved.
  useEffect(() => {
    if (!sidebarOpen && isMobileView) {
      setExpandedItems([]);
    }
  }, [sidebarOpen, isMobileView]);

  // Resolve a sub-item path (handles the /patient-info dynamic token)
  const resolveSubPath = (path: string): string => {
    if (path === '/patient-info') {
      return currentPatient ? `/patients/${currentPatient.id}` : '/patients';
    }
    return path;
  };

  // Active-state check at the sub-item level — robust against overlapping prefixes
  const isSubActive = (subItem: SubItem): boolean => {
    const path = location.pathname;
    if (subItem.dynamicPath === 'patient-detail') {
      // Active only when viewing a specific patient's detail page
      return /^\/patients\/[^/]+/.test(path);
    }
    if (subItem.path === '/patients') {
      // The patient list — exact match only (so it doesn't shadow the detail page)
      return path === '/patients';
    }
    // Query-string sub-items (e.g. /catalogs?type=biomarker): match on pathname
    // + the `type` search param so the right catalog type highlights.
    if (subItem.path.includes('?')) {
      const [base, query] = subItem.path.split('?');
      const expected = new URLSearchParams(query).get('type');
      const actual = new URLSearchParams(location.search).get('type');
      return path === base && (!expected || actual === expected);
    }
    return path === subItem.path || path.startsWith(subItem.path + '/');
  };

  // Active-state check at the top-item level
  const isItemActive = (item: MenuItem): boolean => {
    const path = location.pathname;
    if (item.subItems) {
      return item.subItems.some(isSubActive);
    }
    if (item.path === '/dashboard') {
      return path === '/dashboard' || path === '/';
    }
    return path === item.path || path.startsWith(item.path + '/');
  };

  const handleMouseEnter = (e: React.MouseEvent, item: MenuItem) => {
    if (!effectiveCollapsed) return;
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    const rect = e.currentTarget.getBoundingClientRect();
    setHoveredMenu({ path: item.path, rect, items: item.subItems, labelKey: item.labelKey });
  };

  const handleMouseLeave = () => {
    if (!effectiveCollapsed) return;
    hoverTimeoutRef.current = setTimeout(() => {
      setHoveredMenu(null);
    }, 150);
  };

  const handlePopupMouseEnter = () => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
  };

  const handlePopupMouseLeave = () => {
    hoverTimeoutRef.current = setTimeout(() => {
      setHoveredMenu(null);
    }, 150);
  };

  const menuItems = useMemo<MenuItem[]>(() => [
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
        { path: '/analytics/trends', labelKey: 'common.biomarkers' },
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

    // 6. Administration (merged System + Tenant, role-gated)
    {
      path: '/administration',
      labelKey: 'common.administration',
      icon: ShieldCheck,
      roles: ['SYSTEM_ADMIN', 'ADMIN'],
      subItems: [
        // ── System section ──
        { path: '/admin/system/tenants', labelKey: 'admin.system_tenants', section: 'admin.system_administration', roles: ['SYSTEM_ADMIN'] },
        { path: '/admin/system/users', labelKey: 'admin.users', roles: ['SYSTEM_ADMIN'] },
        { path: '/admin/system/settings', labelKey: 'admin.system_settings', roles: ['SYSTEM_ADMIN'] },
        // ── Tenant section ──
        { path: '/admin/tenant/users', labelKey: 'admin.users', section: 'admin.tenant_management', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
        { path: '/admin/tenant/settings', labelKey: 'admin.tenant_settings', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
        { path: '/patients', labelKey: 'common.patients', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
        { path: '/doctors', labelKey: 'common.doctors', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
        { path: '/organizations', labelKey: 'common.organizations', roles: ['SYSTEM_ADMIN', 'ADMIN'] },
      ],
    },
  ], []);

  const toggleExpand = (path: string) => {
    setExpandedItems(prev => 
      prev.includes(path) ? prev.filter(p => p !== path) : [...prev, path]
    );
  };

  const filteredMenuItems = useMemo(() => {
    return menuItems.filter(item => {
      if (item.roles && user && !item.roles.includes(user.role)) return false;
      return true;
    }).map(item => {
      if (item.subItems) {
        const filteredSubItems = item.subItems.filter(sub => {
          if (sub.roles && user && !sub.roles.includes(user.role)) return false;
          return true;
        });
        // Preserve the section divider on the first surviving item of each section
        const seenSections = new Set<string>();
        const recomputedSections = filteredSubItems.map(sub => {
          if (sub.section) {
            if (seenSections.has(sub.section)) {
              const { section: _section, ...rest } = sub;
              return rest as SubItem;
            }
            seenSections.add(sub.section);
            return sub;
          }
          return sub;
        });
        return { ...item, subItems: recomputedSections.length > 0 ? recomputedSections : undefined };
      }
      return item;
    }).filter(item => {
      // Hide the Administration group entirely if no sub-items survived role filtering
      if (item.roles && item.subItems === undefined) return false;
      return true;
    });
  }, [menuItems, user]);

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      className={`${effectiveCollapsed ? 'w-20' : 'w-64 sm:w-72 lg:w-64'} bg-white dark:bg-dark-surface border-r border-gray-100 dark:border-dark-border flex flex-col h-full shadow-lg lg:shadow-none transition-all duration-300 relative isolate safe-top safe-bottom`}
    >
      {/* Mobile close button — centered on the outer right edge, rounded square */}
      {sidebarOpen && !effectiveCollapsed && (
        <button
          onClick={() => setSidebarOpen(false)}
          aria-label="Close sidebar"
          className="lg:hidden absolute top-1/2 -translate-y-1/2 -right-4 z-50 w-8 h-8 flex items-center justify-center rounded-lg bg-white dark:bg-dark-surface ring-1 ring-black/5 dark:ring-white/10 shadow-md text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:shadow-lg hover:scale-105 active:scale-95 transition-all duration-150"
        >
          <ChevronLeft className="w-5 h-5" />
        </button>
      )}

      <div className={`p-6 flex items-center ${effectiveCollapsed ? 'justify-center' : 'justify-start'} mt-2 mb-4`}>
        <Link to="/" className="flex items-center space-x-3 hover:opacity-80 transition-opacity">
          <img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-9 h-9 shrink-0" alt="Health Assistant Logo" />
          {!effectiveCollapsed && <h1 className="text-xl font-bold text-brand-navy dark:text-white truncate">Health Assistant</h1>}
        </Link>
      </div>

      {/* Invisible hover bridge — keeps the handle visible when the cursor is near the sidebar edge */}
      <div className="hidden lg:block absolute inset-y-0 left-full w-5 z-[955]" aria-hidden="true" />

      {/* Collapse/expand handle — sits outside the sidebar panel and is revealed on hover */}
      <button
        onClick={toggleSidebarCollapse}
        title={effectiveCollapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
        aria-label={effectiveCollapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
        className={`hidden lg:flex absolute top-1/2 -translate-y-1/2 -right-3 z-[960] w-6 h-9 items-center justify-center rounded-lg bg-white dark:bg-dark-surface ring-1 ring-black/5 dark:ring-white/10 shadow-md text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:shadow-lg hover:scale-105 active:scale-95 transition-all duration-150 ${isHovered ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
      >
        {effectiveCollapsed ? (
          <ChevronRight className="w-4 h-4" />
        ) : (
          <ChevronLeft className="w-4 h-4" />
        )}
      </button>

      <nav className="flex-1 px-4 space-y-1 overflow-y-auto no-scrollbar">
        {filteredMenuItems.map((item) => {
          const Icon = item.icon;
          const isActive = isItemActive(item);
          const isExpanded = expandedItems.includes(item.path);
          
          if (item.subItems) {
            if (!effectiveCollapsed) {
              return (
                <div key={item.path} className="space-y-1">
                  <button
                    onClick={() => toggleExpand(item.path)}
                    className={`w-full flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-200 ${
                      isActive
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-brand-cyan dark:text-blue-400 font-bold'
                        : 'text-gray-500 hover:bg-gray-50 dark:text-dark-muted dark:hover:bg-dark-border'
                    }`}
                  >
                    <div className="flex items-center min-w-0 pr-2">
                      <Icon className={`w-5 h-5 mr-3 shrink-0 ${isActive ? 'text-brand-cyan dark:text-blue-400' : 'text-gray-400'}`} />
                      <span className="truncate">{t(item.labelKey)}</span>
                    </div>
                    <ChevronDown className={`w-4 h-4 shrink-0 transition-transform duration-200 ${isActive ? 'text-brand-cyan dark:text-blue-400' : 'text-gray-400'} ${isExpanded ? 'rotate-180' : ''}`} />
                  </button>
                  
                  {isExpanded && (
                    <div className="ml-10 space-y-1 animate-in slide-in-from-top-2 duration-200">
                      {item.subItems.map((subItem) => (
                        <Fragment key={subItem.path}>
                          {subItem.section && (
                            <div className="px-4 pt-3 pb-1 text-[9px] font-black text-gray-300 dark:text-dark-border uppercase tracking-widest">
                              {t(subItem.section)}
                            </div>
                          )}
                          <Link
                            to={resolveSubPath(subItem.path)}
                            aria-current={isSubActive(subItem) ? 'page' : undefined}
                            className={`block px-4 py-2 text-sm rounded-xl transition-all duration-200 ${
                              isSubActive(subItem)
                                ? 'text-brand-cyan dark:text-blue-400 font-black'
                                : 'text-gray-400 hover:text-gray-600 dark:text-dark-muted dark:hover:text-dark-text'
                            }`}
                          >
                            {t(subItem.labelKey)}
                          </Link>
                        </Fragment>
                      ))}
                    </div>
                  )}
                </div>
              );
            } else {
              return (
                <div
                  key={item.path}
                  onMouseEnter={(e) => handleMouseEnter(e, item)}
                  onMouseLeave={handleMouseLeave}
                >
                  <div
                    className={`flex items-center justify-center px-0 py-3 rounded-xl transition-all duration-200 cursor-pointer relative group ${
                      isActive
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-brand-cyan dark:text-blue-400 font-bold shadow-sm'
                        : 'text-gray-500 hover:bg-gray-50 dark:text-dark-muted dark:hover:bg-dark-border hover:translate-x-1'
                    }`}
                    onClick={(e) => {
                       if (hoveredMenu?.path === item.path) {
                         setHoveredMenu(null);
                       } else {
                         handleMouseEnter(e as any, item);
                       }
                    }}
                  >
                    <Icon className={`w-5 h-5 shrink-0 ${isActive ? 'text-brand-cyan dark:text-blue-400' : 'text-gray-400'}`} />
                    <ChevronRight className={`w-3 h-3 absolute right-1 top-1/2 -translate-y-1/2 transition-all duration-200 ${isActive ? 'text-brand-cyan dark:text-blue-400 opacity-100' : 'text-gray-400 opacity-0 group-hover:opacity-100'}`} />
                  </div>
                </div>
              );
            }
          }

          return (
            <div
              key={item.path}
              onMouseEnter={(e) => handleMouseEnter(e, item)}
              onMouseLeave={handleMouseLeave}
            >
              <Link
                to={item.path}
                aria-current={isActive ? 'page' : undefined}
                className={`flex items-center ${effectiveCollapsed ? 'justify-center px-0' : 'px-4'} py-3 rounded-xl transition-all duration-200 ${
                  isActive
                    ? 'bg-blue-50 dark:bg-blue-900/20 text-brand-cyan dark:text-blue-400 font-bold shadow-sm'
                    : 'text-gray-500 hover:bg-gray-50 dark:text-dark-muted dark:hover:bg-dark-border hover:translate-x-1'
                }`}
              >
                <Icon className={`w-5 h-5 ${effectiveCollapsed ? '' : 'mr-3'} shrink-0 ${isActive ? 'text-brand-cyan dark:text-blue-400' : 'text-gray-400'}`} />
                {!effectiveCollapsed && <span className="truncate">{t(item.labelKey)}</span>}
              </Link>
            </div>
          );
        })}
      </nav>

      <div className={`p-4 space-y-2 mt-auto border-t border-gray-50 dark:border-white/5`}>
        <CreateMenu collapsed={effectiveCollapsed} />

        <AppVersion collapsed={effectiveCollapsed} className="pt-1" />
      </div>

      {/* Collapsed Submenu Popup */}
      {hoveredMenu && effectiveCollapsed && (
        hoveredMenu.items ? (
          <div
            onMouseEnter={handlePopupMouseEnter}
            onMouseLeave={handlePopupMouseLeave}
            className="fixed z-[100] bg-white dark:bg-dark-surface rounded-xl shadow-[0_4px_20px_-4px_rgba(0,0,0,0.1)] dark:shadow-[0_4px_20px_-4px_rgba(0,0,0,0.5)] border border-gray-100 dark:border-dark-border py-2 animate-in fade-in zoom-in-95 duration-150 ring-1 ring-black/5 dark:ring-white/5"
            style={{
              top: Math.min(hoveredMenu.rect.top, window.innerHeight - (hoveredMenu.items.length * 40 + 80)),
              left: hoveredMenu.rect.right + 12,
              minWidth: '220px'
            }}
          >
            <div className="px-4 py-2 border-b border-gray-100 dark:border-dark-border mb-1">
              <span className="text-xs font-bold text-gray-500 dark:text-dark-muted uppercase tracking-wider">{t(hoveredMenu.labelKey)}</span>
            </div>
            <div className="px-2 space-y-0.5">
              {hoveredMenu.items.map((subItem) => (
                <Fragment key={subItem.path}>
                  {subItem.section && (
                    <div className="px-3 pt-2 pb-1 text-[9px] font-black text-gray-300 dark:text-dark-border uppercase tracking-widest">
                      {t(subItem.section)}
                    </div>
                  )}
                  <Link
                    to={resolveSubPath(subItem.path)}
                    aria-current={isSubActive(subItem) ? 'page' : undefined}
                    className={`block px-3 py-2 text-sm rounded-lg transition-colors ${
                      isSubActive(subItem)
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-brand-cyan dark:text-blue-400 font-bold'
                        : 'text-gray-600 hover:bg-gray-50 dark:text-dark-text dark:hover:bg-dark-border hover:text-gray-900'
                    }`}
                    onClick={() => setHoveredMenu(null)}
                  >
                    {t(subItem.labelKey)}
                  </Link>
                </Fragment>
              ))}
            </div>
          </div>
        ) : (
          <div
            className="fixed z-[100] bg-white dark:bg-dark-surface text-gray-700 dark:text-gray-200 border border-gray-100 dark:border-dark-border px-3 py-2 rounded-lg shadow-lg text-sm font-medium animate-in fade-in zoom-in-95 duration-150 pointer-events-none whitespace-nowrap"
            style={{
              top: hoveredMenu.rect.top + (hoveredMenu.rect.height / 2) - 18,
              left: hoveredMenu.rect.right + 12,
            }}
          >
            {t(hoveredMenu.labelKey)}
            <div 
              className="absolute w-2 h-2 bg-white dark:bg-dark-surface border-l border-b border-gray-100 dark:border-dark-border rotate-45"
              style={{
                top: '50%',
                left: '-5px',
                transform: 'translateY(-50%) rotate(45deg)'
              }}
            />
          </div>
        )
      )}
    </div>
  );
}

export default Sidebar;

