import { useEffect, useState, useRef, ReactElement, cloneElement, isValidElement } from 'react';
import { useNavigate, Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../../store/slices/authSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { listPatients } from '../../services/fhirService';
import { PatientSelect } from '../patients';
import { Search, ChevronDown, Settings, LogOut, Menu, X, Sparkles, Languages, Sun, Moon, ArrowLeft, Link as LinkIcon, Info } from 'lucide-react';
import { SyncIndicator } from '../ui/SyncIndicator';
import { NotificationBell } from './NotificationBell';
import { Breadcrumbs } from '../ui/Breadcrumbs';
import { useUIStore } from '../../store/slices/uiSlice';

function Header() {
  const { t } = useTranslation();
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();
  const { setPatients, setCurrentPatient } = usePatientStore();
  const { language, setLanguage, theme, setTheme } = useSettingsStore();
  
  const sidebarOpen = useUIStore(state => state.sidebarOpen);
  const toggleSidebar = useUIStore(state => state.toggleSidebar);
  const toggleAIDrawer = useUIStore(state => state.toggleAIDrawer);
  const pageHeaderConfig = useUIStore(state => state.pageHeaderConfig);
  const pageSearchTerm = useUIStore(state => state.pageSearchTerm);
  const setPageSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const isSearchLauncherOpen = useUIStore(state => state.isSearchLauncherOpen);
  
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Helper to get page title from current path (fallback if no PageHeader is used)
  const getPageTitle = () => {
    if (pageHeaderConfig?.title) return pageHeaderConfig.title;
    const path = location.pathname;
    if (path === '/' || path === '/dashboard') return t('common.dashboard');
    return 'Health Assistant';
  };

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    const fetchPatients = async () => {
      // Safely get tenantId
      let currentTenantId = user?.tenant_id;
      if (!currentTenantId) {
        try {
          const token = localStorage.getItem('accessToken');
          if (token) {
             const decoded = JSON.parse(atob(token.split('.')[1]));
             currentTenantId = decoded.tenant_id;
          }
        } catch (e) {}
      }
      
      if (currentTenantId) {
        try {
          // Fetch a larger list to increase chance of finding currentPatient
          const response = await listPatients(currentTenantId, 100);
          const items = response.items || [];
          setPatients(items);
          
          // Refresh or auto-select patient
          if (items.length > 0) {
            const current = usePatientStore.getState().currentPatient;
            
            // Logic 1: If current user is linked to a patient, that patient MUST be the context
            const linkedPatient = items.find((p: any) => p.user_id === user?.id);
            
            if (linkedPatient) {
              if (!current || current.id !== linkedPatient.id) {
                setCurrentPatient(linkedPatient);
                return; // Priority 1 satisfied
              }
            }

            // Logic 2: If no link but we have a stale context from another tenant, clear it
            if (current && !items.find(p => p.id === current.id)) {
              setCurrentPatient(null);
              return;
            }

            // Logic 3: If no patient is selected yet and we are an admin, we don't auto-select random patients
            // Only auto-select if it's the ONLY patient in the tenant (Home user context)
            if (!current && items.length === 1) {
              setCurrentPatient(items[0]);
            }
            
            // Update existing currentPatient data if it was already correct
            if (current) {
              const latest = items.find(p => p.id === current.id);
              if (latest) {
                // Update if there are meaningful differences
                if (latest.mrn !== current.mrn || 
                    latest.birthDate !== current.birthDate || 
                    (latest as any).birth_date !== (current as any).birth_date ||
                    JSON.stringify(latest.name) !== JSON.stringify(current.name)) {
                  setCurrentPatient(latest);
                }
              }
            }
          } else {
            // No patients available at all
            setCurrentPatient(null);
          }
        } catch (error) {
          console.error("Failed to fetch patients for header dropdown:", error);
        }
      }
    };
    
    fetchPatients();
  }, [user?.tenant_id, setPatients, setCurrentPatient]); // Removed currentPatient from dependencies

  const handleLanguageToggle = () => {
    const newLang = language === 'en' ? 'el' : 'en';
    setLanguage(newLang);
  };

  const handleThemeToggle = () => {
    setTheme(theme === 'light' ? 'dark' : 'light');
  };

  return (
    <header className="bg-white dark:bg-dark-surface border-b border-gray-100 dark:border-dark-border px-4 md:px-6 py-2 md:py-2.5 flex items-center justify-between z-[500] sticky top-0 shadow-sm transition-all duration-300">
      <div className="flex items-center space-x-2 md:space-x-4 flex-shrink-0">
        {/* Mobile/Tablet Sidebar Toggle */}
        <button 
          onClick={toggleSidebar}
          className="lg:hidden p-2 -ml-2 text-gray-400 hover:text-gray-600 dark:text-dark-muted dark:hover:text-dark-text transition-colors"
        >
          {sidebarOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>

        {/* Dynamic Header Section (Title/Icon/Back) */}
        <div className="flex items-center bg-gray-50/50 dark:bg-dark-bg/30 p-1.5 pr-4 rounded-2xl border border-gray-100/50 dark:border-dark-border/20 backdrop-blur-sm shadow-sm min-w-0 max-w-sm sm:max-w-md">
          {pageHeaderConfig?.showBackButton && (
            <>
              <button 
                onClick={() => navigate(-1)}
                className="w-9 h-9 flex items-center justify-center text-gray-500 hover:text-blue-600 dark:text-gray-400 dark:hover:text-blue-400 transition-all hover:bg-white dark:hover:bg-dark-surface rounded-xl active:scale-90 group/back ml-0.5"
                title="Go Back"
              >
                <ArrowLeft className="w-5 h-5 group-hover/back:-translate-x-1 transition-transform" />
              </button>
              <div className="w-px h-6 bg-gray-200/60 dark:bg-dark-border/60 mx-2" />
            </>
          )}

          <div className="flex items-center space-x-3">
            {pageHeaderConfig?.icon && (
              <div className={`hidden sm:flex w-9 h-9 ${isValidElement(pageHeaderConfig.icon) && pageHeaderConfig.icon.type === 'img' ? '' : 'bg-blue-600'} rounded-xl items-center justify-center text-white border border-blue-500/20 shadow-md flex-shrink-0 overflow-hidden`}>
                {isValidElement(pageHeaderConfig.icon) && pageHeaderConfig.icon.type === 'img' ? (
                  pageHeaderConfig.icon
                ) : (
                  cloneElement(pageHeaderConfig.icon as ReactElement, { className: "w-5 h-5" })
                )}
              </div>
            )}
            <div className="flex flex-col min-w-0">
              {pageHeaderConfig?.breadcrumbs && (
                <Breadcrumbs 
                  items={pageHeaderConfig.breadcrumbs} 
                  currentLabel={pageHeaderConfig.title}
                />
              )}
              <h1 className="text-base md:text-lg font-black text-[#1a2b4b] dark:text-dark-text tracking-tight truncate leading-none">
                {getPageTitle()}
              </h1>
              {pageHeaderConfig?.subtitle && (
                <div className="hidden md:block text-gray-400 dark:text-dark-muted text-[9px] font-bold uppercase tracking-[0.2em] mt-1 opacity-70 leading-normal" title={typeof pageHeaderConfig.subtitle === 'string' ? pageHeaderConfig.subtitle : undefined}>
                  {pageHeaderConfig.subtitle}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Center Section: Removed old global search to save space */}
      <div className="flex-1 max-w-2xl mx-6 hidden md:flex items-center justify-center">
      </div>

      {/* Right Section: Global Tools ONLY (Actions moved to Toolbar) */}
      <div className="flex items-center space-x-2 md:space-x-4">
        {/* Mobile Search Toggle */}
        <button 
          className={`md:hidden p-2 transition-colors relative ${pageSearchTerm && !isSearchLauncherOpen ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400 hover:text-gray-500'}`}
          onClick={() => useUIStore.getState().setSearchLauncherOpen(true)}
        >
          <Search className="h-5 w-5" />
          {pageSearchTerm && !isSearchLauncherOpen && (
            <span className="absolute top-1 right-1 w-2 h-2 bg-blue-500 rounded-full border border-white dark:border-dark-surface" />
          )}
        </button>

        {/* Global Patient Selector */}
        <div className="hidden sm:flex items-center">
          <PatientSelect />
        </div>

        <div className="flex items-center space-x-1 md:space-x-2">
          {/* Desktop Search Launcher Button */}
          <div className="hidden md:flex items-center">
            {pageSearchTerm && !isSearchLauncherOpen ? (
              <div className="flex items-stretch bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-xl min-h-[36px]">
                <button
                  onClick={() => useUIStore.getState().setSearchLauncherOpen(true)}
                  className="flex items-center pl-2 pr-1.5 py-1 text-blue-700 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 rounded-l-xl transition-colors text-left"
                  title={t('common.edit_search', 'Edit search')}
                >
                  <Search className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 self-start" />
                  <span className="text-[10px] sm:text-xs font-bold w-16 sm:w-24 md:w-32 whitespace-normal break-words line-clamp-2 leading-tight ml-1.5">
                    {pageSearchTerm}
                  </span>
                </button>
                <div className="w-px bg-blue-200 dark:bg-blue-800 my-1" />
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setPageSearchTerm('');
                  }}
                  className="flex items-center justify-center px-1.5 hover:text-blue-600 dark:hover:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/40 rounded-r-xl transition-colors text-blue-400 flex-shrink-0"
                  title={t('common.clear_search', 'Clear search')}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <button
                onClick={() => useUIStore.getState().setSearchLauncherOpen(true)}
                className="flex items-center space-x-2 px-3 h-9 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border hover:border-gray-200 dark:hover:border-gray-700 rounded-xl text-gray-500 dark:text-dark-muted transition-colors active:scale-95 group"
                title={t('common.search', 'Search')}
              >
                <Search className="w-4 h-4 group-hover:text-blue-500 transition-colors" />
                <span className="text-xs font-medium mr-1">{t('common.search', 'Search...')}</span>
              </button>
            )}
          </div>

          <button 
            onClick={() => toggleAIDrawer()}
            className="relative p-2 text-indigo-400 hover:text-indigo-600 dark:text-indigo-500/80 dark:hover:text-indigo-400 transition-all hover:scale-110 active:scale-95"
            title="AI Assistant"
          >
            <Sparkles className="h-5 w-5" />
            <span className="absolute top-0.5 right-0.5 flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
            </span>
          </button>

          <NotificationBell />

          <div className="relative" ref={menuRef}>
            <div 
              className="flex items-center space-x-2 cursor-pointer group p-1 rounded-full hover:bg-gray-50 dark:hover:bg-dark-bg transition-colors"
              onClick={() => setIsUserMenuOpen(!isUserMenuOpen)}
            >
              <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold text-xs group-hover:bg-blue-200 transition-colors">
                {user?.email?.[0]?.toUpperCase() || 'A'}
              </div>
              <ChevronDown className={`h-4 w-4 text-gray-400 transition-transform duration-200 ${isUserMenuOpen ? 'rotate-180' : ''}`} />
            </div>

            {isUserMenuOpen && (
              <div className="absolute right-0 mt-3 w-64 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-xl z-[510] py-2 animate-in fade-in slide-in-from-top-2 duration-200 overflow-hidden">
                {/* Mobile/Compact Patient Selector in Menu */}
                <div className="sm:hidden px-2 pb-2 mb-1 border-b border-gray-50 dark:border-dark-border">
                  <PatientSelect className="border-none bg-transparent shadow-none" align="right" />
                </div>

                <div className="px-4 py-3 border-b border-gray-50 dark:border-dark-border mb-1 bg-gray-50/50 dark:bg-dark-bg/30">
                  <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('common.account')}</p>
                  <p className="text-sm font-bold text-gray-700 dark:text-dark-text truncate mt-1">{user?.email}</p>
                  <div className="flex items-center justify-between mt-1">
                    <p className="text-[10px] text-blue-500 font-bold uppercase">
                      {user?.role === 'SYSTEM_ADMIN' ? t('admin.role_system_admin') : 
                       user?.role === 'ADMIN' ? t('admin.role_admin') : 
                       user?.role === 'MANAGER' ? t('admin.role_manager') : 
                       t('admin.role_user')}
                    </p>
                  </div>
                </div>

                <div className="px-3 py-2 border-b border-gray-50 dark:border-dark-border mb-1">
                  <SyncIndicator className="w-full" />
                </div>
                
                <div className="p-1 space-y-0.5">
                  <button 
                    onClick={handleLanguageToggle}
                    className="w-full flex items-center px-4 py-2 text-sm text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-colors"
                  >
                    <Languages className="w-4 h-4 mr-3 text-gray-400" />
                    <span className="font-medium">{language === 'en' ? t('common.greek') : t('common.english')}</span>
                  </button>

                  <button 
                    onClick={handleThemeToggle}
                    className="w-full flex items-center px-4 py-2 text-sm text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-colors"
                  >
                    {theme === 'light' ? (
                      <>
                        <Moon className="w-4 h-4 mr-3 text-gray-400" />
                        <span className="font-medium">{t('common.dark_mode')}</span>
                      </>
                    ) : (
                      <>
                        <Sun className="w-4 h-4 mr-3 text-gray-400" />
                        <span className="font-medium">{t('common.light_mode')}</span>
                      </>
                    )}
                  </button>

                  <Link 
                    to="/settings" 
                    onClick={() => setIsUserMenuOpen(false)}
                    className="w-full flex items-center px-4 py-2 text-sm text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-colors"
                  >
                    <Settings className="w-4 h-4 mr-3 text-gray-400" />
                    <span className="font-medium">{t('common.settings')}</span>
                  </Link>

                  <Link 
                    to="/settings/integrations" 
                    onClick={() => setIsUserMenuOpen(false)}
                    className="w-full flex items-center px-4 py-2 text-sm text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-colors"
                  >
                    <LinkIcon className="w-4 h-4 mr-3 text-gray-400" />
                    <span className="font-medium">Integrations</span>
                  </Link>
                  
                  <Link 
                    to="/about" 
                    onClick={() => setIsUserMenuOpen(false)}
                    className="w-full flex items-center px-4 py-2 text-sm text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-colors"
                  >
                    <Info className="w-4 h-4 mr-3 text-gray-400" />
                    <span className="font-medium">{t('common.about')}</span>
                  </Link>
                  
                  <div className="h-px bg-gray-50 dark:bg-dark-border my-1 mx-2" />

                  <button 
                    onClick={async () => {
                      await logout();
                      setIsUserMenuOpen(false);
                    }}
                    className="w-full flex items-center px-4 py-2 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-colors"
                  >
                    <LogOut className="w-4 h-4 mr-3" />
                    <span className="font-bold">{t('common.logout')}</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Full Screen Mobile Search Overlay removed. Replaced by SearchLauncher. */}
    </header>
  );
}

export default Header;
