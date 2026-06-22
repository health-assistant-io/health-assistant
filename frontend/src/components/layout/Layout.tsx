import { Outlet } from 'react-router-dom';
import Header from './Header';
import Sidebar from './Sidebar';
import TenantSwitchBanner from './TenantSwitchBanner';
import { AIDrawer } from './AIDrawer';
import { ConfirmationModal } from '../ui/ConfirmationModal';
import { SearchLauncher } from '../ui/SearchLauncher';
import { useUIStore } from '../../store/slices/uiSlice';
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

function Layout() {
  const sidebarOpen = useUIStore(state => state.sidebarOpen);
  const setSidebarOpen = useUIStore(state => state.setSidebarOpen);
  const setSidebarCollapsed = useUIStore(state => state.setSidebarCollapsed);
  const sidebarCollapsed = useUIStore(state => state.sidebarCollapsed);
  const setLastNonAiPath = useUIStore(state => state.setLastNonAiPath);
  const aiDrawerOpen = useUIStore(state => state.aiDrawerOpen);
  const setAIDrawerOpen = useUIStore(state => state.setAIDrawerOpen);
  const location = useLocation();
  
  const lastWidthCategory = useRef<'small' | 'medium' | 'large' | null>(null);
  const wasAutoCollapsed = useRef(false);

  // Auto-collapse sidebar on smaller desktop screens
  useEffect(() => {
    const handleResize = () => {
      const width = window.innerWidth;
      let currentCategory: 'small' | 'medium' | 'large';
      
      if (width < 1024) currentCategory = 'small';
      else if (width < 1280) currentCategory = 'medium';
      else currentCategory = 'large';

      // Only trigger state changes when crossing a breakpoint category
      if (lastWidthCategory.current !== currentCategory) {
        if (currentCategory === 'medium') {
          if (!sidebarCollapsed) {
            setSidebarCollapsed(true);
            wasAutoCollapsed.current = true;
          }
        } else if (currentCategory === 'large') {
          if (sidebarCollapsed && wasAutoCollapsed.current) {
            setSidebarCollapsed(false);
          }
          wasAutoCollapsed.current = false;
        }
        lastWidthCategory.current = currentCategory;
      }
    };
    
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [setSidebarCollapsed, sidebarCollapsed]);

  // Reset auto-collapse flag if user manually toggles sidebar
  useEffect(() => {
    const width = window.innerWidth;
    // If user expands it while in medium range, or collapses it while in large range, 
    // it's a manual action and we should stop auto-expanding/collapsing.
    if (width >= 1280 && sidebarCollapsed) {
      wasAutoCollapsed.current = false;
    }
    if (width >= 1024 && width < 1280 && !sidebarCollapsed) {
      wasAutoCollapsed.current = false;
    }
  }, [sidebarCollapsed]);

  // Track last non-AI path and close sidebar on navigation
  useEffect(() => {
    setSidebarOpen(false);
    
    if (!location.pathname.startsWith('/ai-assistant')) {
      setLastNonAiPath(location.pathname + location.search);
    }
  }, [location.pathname, location.search, setSidebarOpen, setLastNonAiPath]);

  const isAiPage = location.pathname.includes('/ai-assistant');

  return (
    <div className="flex h-screen bg-gray-50 dark:bg-dark-bg overflow-hidden relative">
      {/* Sidebar Overlay (Mobile) */}
      {sidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/50 z-[900] lg:hidden backdrop-blur-sm transition-opacity"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      
      {/* Sidebar Container */}
      <div className={`fixed inset-y-0 left-0 z-[950] transform lg:relative lg:translate-x-0 transition-all duration-300 ease-in-out ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}>
        <Sidebar />
      </div>

      <div className="flex flex-col flex-1 min-w-0 overflow-hidden relative">
        <TenantSwitchBanner />
        <Header />
        <main className={`flex-1 flex flex-col min-w-0 relative ${isAiPage ? 'bg-white dark:bg-dark-bg p-0 overflow-hidden' : 'overflow-y-auto overflow-x-hidden pt-1 sm:pt-2 md:pt-3 lg:pt-4 pb-4 sm:pb-6 md:pb-8 lg:pb-10 px-2 sm:px-4 md:px-6 lg:px-8'}`}>
          <div className="flex-1 flex flex-col min-h-0 relative w-full">
            <Outlet />
          </div>
        </main>
      </div>

      <AIDrawer 
        isOpen={aiDrawerOpen} 
        onClose={() => setAIDrawerOpen(false)} 
      />
      <SearchLauncher />
      <ConfirmationModal />
    </div>
  );
}

export default Layout;