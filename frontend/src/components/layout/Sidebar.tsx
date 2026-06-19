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
  Info
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useState, useMemo } from 'react';
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
        { path: '/admin/tenants', labelKey: 'admin.system_tenants' },
        { path: '/admin/system/catalogs', labelKey: 'Clinical Ontology' },
        { path: '/admin/system/ai-config', labelKey: 'admin.system_ai_config' },
        { path: '/admin/system/integrations', labelKey: 'System Integrations' },
      ]
    },
    { 
      path: '/admin/tenant', 
      labelKey: 'admin.tenant_management', 
      icon: Building2,
      roles: ['SYSTEM_ADMIN', 'ADMIN'],
      subItems: [
        { path: '/admin/users', labelKey: 'admin.users' },
        { path: '/admin/tenant/ai-config', labelKey: 'admin.tenant_ai_config' },
        { path: '/patients', labelKey: 'common.patients' },
        { path: '/doctors', labelKey: 'common.doctors' },
        { path: '/organizations', labelKey: 'common.organizations' },
      ]
    },
    { 
      path: '/settings', 
      labelKey: 'common.settings', 
      icon: User,
      subItems: [
        { path: '/settings/profile', labelKey: 'common.profile' },
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
        return {
          ...item,
          subItems: item.subItems.filter(sub => {
            if (sub.roles && user && !sub.roles.includes(user.role)) return false;
            return true;
          })
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
          const targetPath = item.path === '/patient-info' && currentPatient 
            ? `/patients/${currentPatient.id}` 
            : item.path;

          const isActive = location.pathname === targetPath || 
            (item.path !== '/dashboard' && location.pathname.startsWith(item.path)) ||
            (item.path === '/patient-info' && location.pathname.startsWith('/patients/') && currentPatient && location.pathname.includes(currentPatient.id));
          
          const isExpanded = expandedItems.includes(item.path);
          
          if (item.subItems && !sidebarCollapsed) {
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
                  <div className="flex items-center">
                    <Icon className={`w-5 h-5 mr-3 shrink-0 ${isActive ? 'text-[#0088CC] dark:text-blue-400' : 'text-gray-400'}`} />
                    <span className="truncate">{t(item.labelKey)}</span>
                  </div>
                  <ChevronDown className={`w-4 h-4 transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`} />
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
          }

          return (
            <Link
              key={item.path}
              to={targetPath}
              title={sidebarCollapsed ? t(item.labelKey) : ''}
              className={`flex items-center ${sidebarCollapsed ? 'justify-center px-0' : 'px-4'} py-3 rounded-xl transition-all duration-200 ${
                isActive
                  ? 'bg-blue-50 dark:bg-blue-900/20 text-[#0088CC] dark:text-blue-400 font-bold shadow-sm'
                  : 'text-gray-500 hover:bg-gray-50 dark:text-dark-muted dark:hover:bg-dark-border hover:translate-x-1'
              }`}
            >
              <Icon className={`w-5 h-5 ${sidebarCollapsed ? '' : 'mr-3'} shrink-0 ${isActive ? 'text-[#0088CC] dark:text-blue-400' : 'text-gray-400'}`} />
              {!sidebarCollapsed && <span className="truncate">{t(item.labelKey)}</span>}
            </Link>
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
    </div>
  );
}

export default Sidebar;
