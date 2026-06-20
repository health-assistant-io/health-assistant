import { Link, useLocation } from 'react-router-dom';
import { 
  LayoutDashboard, 
  Image as ImageIcon, 
  FileText, 
  Users, 
  PlusCircle, 
  Stethoscope, 
  ShieldAlert, 
  Pill, 
  Bell,
  Calendar,
  X, 
  Sparkles, 
  ChevronLeft, 
  ChevronRight, 
  BarChart3 as AnalyticsIcon,
  ChevronDown,
  Activity,
  User,
  ShieldCheck,
  Building2,
  Globe,
  Info,
  Settings as SettingsIcon
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useState, useMemo, useRef } from 'react';
import AppVersion from '../ui/AppVersion';

interface MenuItem {
  path: string;
  labelKey: string;
  icon: any;
  subItems?: { path: string; labelKey: string; roles?: string[] }[];
  requiresPatient?: boolean;
  roles?: string[];
}

function Sidebar() {
  const { t } = useTranslation();
  const location = useLocation();
  const setSidebarOpen = useUIStore(state => state.setSidebarOpen);
  const sidebarCollapsed = useUIStore(state => state.sidebarCollapsed);
  const toggleSidebarCollapse = useUIStore(state => state.toggleSidebarCollapse);
  const { currentPatient } = usePatientStore();
  const user = useAuthStore(state => state.user);
  const theme = useSettingsStore(state => state.theme);
  const [expandedItems, setExpandedItems] = useState<string[]>(['/analytics', '/admin/system', '/admin/tenant']);
  const [hoveredMenu, setHoveredMenu] = useState<{ path: string; rect: DOMRect, items?: any[], labelKey: string } | null>(null);
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleMouseEnter = (e: React.MouseEvent, item: MenuItem) => {
    if (!sidebarCollapsed) return;
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    const rect = e.currentTarget.getBoundingClientRect();
    setHoveredMenu({ path: item.path, rect, items: item.subItems, labelKey: item.labelKey });
  };

  const handleMouseLeave = () => {
    if (!sidebarCollapsed) return;
    hoverTimeoutRef.current = setTimeout(() => {
      setHoveredMenu(null);
    }, 150); // slight delay to allow moving to popup
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
    { path: '/dashboard', labelKey: 'common.dashboard', icon: LayoutDashboard },
    { path: '/patient-info', labelKey: 'common.patient_info', icon: User, requiresPatient: true },
    { path: '/ai-assistant', labelKey: 'common.ai_assistant', icon: Sparkles },
    { path: '/alerts', labelKey: 'common.clinical_alerts', icon: ShieldAlert },
    { 
      path: '/analytics', 
      labelKey: 'common.analytics', 
      icon: AnalyticsIcon,
      subItems: [
        { path: '/analytics/trends', labelKey: 'common.biomarker_trends' },
        { path: '/analytics/correlative', labelKey: 'common.correlative_analytics' }
      ]
    },
    { path: '/documents', labelKey: 'common.documents_explorer', icon: ImageIcon },
    { path: '/examinations', labelKey: 'common.examinations', icon: FileText },
    { path: '/calendar', labelKey: 'common.calendar', icon: Calendar },
    { path: '/events', labelKey: 'events.title', icon: Activity },
    { path: '/medications', labelKey: 'common.medications', icon: Pill },
    { 
      path: '/admin/system', 
      labelKey: 'admin.system_administration', 
      icon: Globe,
      roles: ['SYSTEM_ADMIN'],
      subItems: [
        { path: '/admin/system/tenants', labelKey: 'admin.system_tenants' },
        { path: '/admin/system/users', labelKey: 'admin.users' },
        { path: '/admin/system/catalogs', labelKey: 'Clinical Ontology' },
        { path: '/admin/system/ai-config', labelKey: 'admin.system_ai_config' },
        { path: '/admin/system/integrations', labelKey: 'System Integrations' },
        { path: '/admin/system/settings', labelKey: 'admin.system_settings' },
      ]
    },
    {
      path: '/admin/tenant',
      labelKey: 'admin.tenant_management',
      icon: Building2,
      roles: ['SYSTEM_ADMIN', 'ADMIN'],
      subItems: [
        { path: '/admin/tenant/users', labelKey: 'admin.users' },
        { path: '/admin/tenant/ai-config', labelKey: 'admin.tenant_ai_config' },
        { path: '/admin/tenant/settings', labelKey: 'admin.tenant_settings' },
        { path: '/patients', labelKey: 'common.patients' },
        { path: '/doctors', labelKey: 'common.doctors' },
        { path: '/organizations', labelKey: 'common.organizations' },
      ]
    },
    {
      path: '/settings',
      labelKey: 'common.settings',
      icon: SettingsIcon,
      subItems: [
        { path: '/settings/profile', labelKey: 'common.profile' },
        { path: '/settings/appearance', labelKey: 'settings.appearance_short' },
        { path: '/settings/integrations', labelKey: 'common.integrations' },
        { path: '/settings/ai-config', labelKey: 'common.personal_ai_keys' },
        { path: '/settings/export-import', labelKey: 'backup.title', roles: ['ADMIN', 'SYSTEM_ADMIN'] },
        { path: '/notifications', labelKey: 'common.notifications' }
      ]
    },
  ], [currentPatient]);

  const toggleExpand = (path: string) => {
    setExpandedItems(prev => 
      prev.includes(path) ? prev.filter(p => p !== path) : [...prev, path]
    );
  };

  const filteredMenuItems = useMemo(() => {
    return menuItems.filter(item => {
      // Check top level role
      if (item.roles && user && !item.roles.includes(user.role)) return false;
      return true;
    }).map(item => {
      // Filter sub items
      if (item.subItems) {
        const filteredSubItems = item.subItems.filter(sub => {
          if (sub.roles && user && !sub.roles.includes(user.role)) return false;
          return true;
        });
        
        return {
          ...item,
          subItems: filteredSubItems.length > 0 ? filteredSubItems : undefined
        };
      }
      return item;
    });
  }, [menuItems, user]);

  return (
    <div className={`${sidebarCollapsed ? 'w-20' : 'w-64 sm:w-72 lg:w-64'} bg-white dark:bg-dark-surface border-r border-gray-100 dark:border-dark-border flex flex-col h-full shadow-lg lg:shadow-none transition-all duration-300 relative`}>
      <div className={`p-6 flex items-center ${sidebarCollapsed ? 'justify-center' : 'justify-between'} mt-2 mb-4`}>
        <div className="flex items-center space-x-3">
          <img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-9 h-9 shrink-0" alt="Health Assistant Logo" />
          {!sidebarCollapsed && <h1 className="text-xl font-bold text-[#1a2b4b] dark:text-white truncate">Health Assistant</h1>}
        </div>
        
        {/* Mobile Close Button */}
        {!sidebarCollapsed && (
          <button 
            onClick={() => setSidebarOpen(false)}
            className="lg:hidden p-2 text-gray-400 hover:text-gray-600 dark:text-dark-muted dark:hover:text-dark-text"
          >
            <X className="h-6 w-6" />
          </button>
        )}
      </div>

      <nav className="flex-1 px-4 space-y-1 overflow-y-auto no-scrollbar">
        {filteredMenuItems.map((item) => {
          // Skip patient-info if no patient selected
          if (item.requiresPatient && !currentPatient) return null;

          const Icon = item.icon;
          let targetPath = item.path === '/patient-info' && currentPatient 
            ? `/patients/${currentPatient.id}` 
            : item.path;

          const isActive = location.pathname === targetPath || 
            (item.path !== '/dashboard' && location.pathname.startsWith(item.path)) ||
            (item.path === '/patient-info' && location.pathname.startsWith('/patients/') && currentPatient && location.pathname.includes(currentPatient.id));
          
          const isExpanded = expandedItems.includes(item.path);
          
          if (item.subItems) {
            if (!sidebarCollapsed) {
              return (
                <div key={item.path} className="space-y-1">
                  <button
                    onClick={() => toggleExpand(item.path)}
                    className={`w-full flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-200 ${
                      isActive
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-[#0088CC] dark:text-blue-400 font-bold'
                        : 'text-gray-500 hover:bg-gray-50 dark:text-dark-muted dark:hover:bg-dark-border'
                    }`}
                  >
                    <div className="flex items-center min-w-0 pr-2">
                      <Icon className={`w-5 h-5 mr-3 shrink-0 ${isActive ? 'text-[#0088CC] dark:text-blue-400' : 'text-gray-400'}`} />
                      <span className="truncate">{t(item.labelKey)}</span>
                    </div>
                    <ChevronDown className={`w-4 h-4 shrink-0 transition-transform duration-200 ${isActive ? 'text-[#0088CC] dark:text-blue-400' : 'text-gray-400'} ${isExpanded ? 'rotate-180' : ''}`} />
                  </button>
                  
                  {isExpanded && (
                    <div className="ml-10 space-y-1 animate-in slide-in-from-top-2 duration-200">
                      {item.subItems.map((subItem) => {
                        const isSubActive = location.pathname === subItem.path;
                        return (
                          <Link
                            key={subItem.path}
                            to={subItem.path}
                            className={`block px-4 py-2 text-sm rounded-xl transition-all duration-200 ${
                              isSubActive
                                ? 'text-[#0088CC] dark:text-blue-400 font-black'
                                : 'text-gray-400 hover:text-gray-600 dark:text-dark-muted dark:hover:text-dark-text'
                            }`}
                          >
                            {t(subItem.labelKey)}
                          </Link>
                        );
                      })}
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
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-[#0088CC] dark:text-blue-400 font-bold shadow-sm'
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
                    <Icon className={`w-5 h-5 shrink-0 ${isActive ? 'text-[#0088CC] dark:text-blue-400' : 'text-gray-400'}`} />
                    <ChevronRight className={`w-3 h-3 absolute right-1 top-1/2 -translate-y-1/2 transition-all duration-200 ${isActive ? 'text-[#0088CC] dark:text-blue-400 opacity-100' : 'text-gray-400 opacity-0 group-hover:opacity-100'}`} />
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
                to={targetPath}
                className={`flex items-center ${sidebarCollapsed ? 'justify-center px-0' : 'px-4'} py-3 rounded-xl transition-all duration-200 ${
                  isActive
                    ? 'bg-blue-50 dark:bg-blue-900/20 text-[#0088CC] dark:text-blue-400 font-bold shadow-sm'
                    : 'text-gray-500 hover:bg-gray-50 dark:text-dark-muted dark:hover:bg-dark-border hover:translate-x-1'
                }`}
              >
                <Icon className={`w-5 h-5 ${sidebarCollapsed ? '' : 'mr-3'} shrink-0 ${isActive ? 'text-[#0088CC] dark:text-blue-400' : 'text-gray-400'}`} />
                {!sidebarCollapsed && <span className="truncate">{t(item.labelKey)}</span>}
              </Link>
            </div>
          );
        })}
      </nav>

      <div className={`p-4 space-y-2 mt-auto border-t border-gray-50 dark:border-white/5`}>
        <Link 
          to="/examinations/upload"
          title={sidebarCollapsed ? t('common.new_examination') : ''}
          className={`w-full flex items-center justify-center bg-[#0088CC] hover:bg-[#0077B3] text-white ${sidebarCollapsed ? 'h-12 w-12 mx-auto' : 'px-4 py-3'} rounded-xl font-bold transition-all shadow-md shadow-blue-100 dark:shadow-none active:scale-95`}
        >
          <PlusCircle className={`w-5 h-5 ${sidebarCollapsed ? '' : 'mr-2'} shrink-0`} />
          {!sidebarCollapsed && <span className="truncate">{t('common.new_examination')}</span>}
        </Link>

        {/* Collapse Toggle Button (Integrated at bottom) */}
        <button
          onClick={toggleSidebarCollapse}
          className={`hidden lg:flex items-center ${sidebarCollapsed ? 'justify-center h-12 w-12 mx-auto' : 'px-4 py-3'} w-full text-gray-400 hover:text-indigo-600 hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-all group`}
          title={sidebarCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
        >
          {sidebarCollapsed ? (
            <ChevronRight className="w-5 h-5 group-hover:translate-x-0.5 transition-transform" />
          ) : (
            <>
              <ChevronLeft className="w-5 h-5 mr-3 group-hover:-translate-x-0.5 transition-transform" />
              <span className="text-xs font-bold uppercase tracking-widest">Collapse Menu</span>
            </>
          )}
        </button>

        {/* Version Display */}
        <AppVersion collapsed={sidebarCollapsed} className="pt-1" />
      </div>

      {/* Collapsed Submenu Popup */}
      {hoveredMenu && sidebarCollapsed && (
        hoveredMenu.items ? (
          <div
            onMouseEnter={handlePopupMouseEnter}
            onMouseLeave={handlePopupMouseLeave}
            className="fixed z-[100] bg-white dark:bg-dark-surface rounded-xl shadow-[0_4px_20px_-4px_rgba(0,0,0,0.1)] dark:shadow-[0_4px_20px_-4px_rgba(0,0,0,0.5)] border border-gray-100 dark:border-dark-border py-2 animate-in fade-in zoom-in-95 duration-150 ring-1 ring-black/5 dark:ring-white/5"
            style={{
              top: Math.min(hoveredMenu.rect.top, window.innerHeight - (hoveredMenu.items.length * 40 + 60)),
              left: hoveredMenu.rect.right + 12, // slightly more spacing
              minWidth: '220px'
            }}
          >
            <div className="px-4 py-2 border-b border-gray-100 dark:border-dark-border mb-1">
              <span className="text-xs font-bold text-gray-500 dark:text-dark-muted uppercase tracking-wider">{t(hoveredMenu.labelKey)}</span>
            </div>
            <div className="px-2 space-y-0.5">
              {hoveredMenu.items.map((subItem) => {
                const isSubActive = location.pathname === subItem.path;
                return (
                  <Link
                    key={subItem.path}
                    to={subItem.path}
                    className={`block px-3 py-2 text-sm rounded-lg transition-colors ${
                      isSubActive
                        ? 'bg-blue-50 dark:bg-blue-900/20 text-[#0088CC] dark:text-blue-400 font-bold'
                        : 'text-gray-600 hover:bg-gray-50 dark:text-dark-text dark:hover:bg-dark-border hover:text-gray-900'
                    }`}
                    onClick={() => setHoveredMenu(null)}
                  >
                    {t(subItem.labelKey)}
                  </Link>
                );
              })}
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
            {/* Small triangle pointer */}
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
