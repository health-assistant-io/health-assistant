import { Routes, Route } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { useRegisterSW } from 'virtual:pwa-register/react';
import Layout from './components/layout/Layout';
import Login from './pages/Auth/Login';
import Dashboard from './pages/Dashboard/Dashboard';
import ClinicalAlerts from './pages/Dashboard/ClinicalAlerts';
import { BiomarkerTrends, CorrelativeAnalytics } from './pages/Analytics';
import Documents from './pages/Documents/DocumentList';
import DocumentDetail from './pages/Documents/DocumentDetail';
import Examinations from './pages/Examinations/ExaminationList';
import ExaminationUpload from './pages/Examinations/ExaminationUpload';
import ExaminationDetail from './pages/Examinations/ExaminationDetail';
import ClinicalEventList from './pages/Events/ClinicalEventList';
import ClinicalEventDetail from './pages/Events/ClinicalEventDetail';
import { ExaminationCategoryManager } from './pages/Examinations/ExaminationCategoryManager';
import TaskManager from './pages/TaskManager';
import Patients from './pages/Patients/PatientList';
import PatientDetail from './pages/Patients/PatientDetail';
import Telemetry from './pages/Telemetry/TelemetryData';
import Doctors from './pages/Doctors/DoctorList';
import MedicationList from './pages/Medications/MedicationList';
import MedicationCatalog from './pages/Medications/MedicationCatalog';
import MedicationDetail from './pages/Medications/MedicationDetail';
import CalendarPage from './pages/Calendar/CalendarPage';
import NotificationManagement from './pages/Notifications/NotificationManagement';
import BiomarkerCatalog from './pages/Biomarkers/BiomarkerCatalog';
import BiomarkerDetail from './pages/Biomarkers/BiomarkerDetail';
import AIChatPage from './pages/AI/AIChat';
import DoctorDetail from './pages/Doctors/DoctorDetail';
import Organizations from './pages/Organizations/OrganizationList';
import OrganizationDetail from './pages/Organizations/OrganizationDetail';
import AboutPage from './pages/About/AboutPage';
import Settings from './pages/Settings/Profile';
import Integrations from './pages/Settings/Integrations';
import IntegrationDetail from './pages/Settings/IntegrationDetail';
import ExportImport from './pages/Settings/ExportImport';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import UserManagement from './pages/Admin/UserManagement';
import UserDetail from './pages/Admin/UserDetail';
import TenantManagement from './pages/Admin/TenantManagement';
import CatalogManagement from './pages/Admin/CatalogManagement';
import SystemIntegrations from './pages/Admin/SystemIntegrations';

import { AIConfig } from './pages/Settings/AIConfig';
import { useProtectedRoute } from './hooks/useProtectedRoute';
import { useAuthStore } from './store/slices/authSlice';
import { useSettingsStore } from './store/slices/settingsSlice';
import { getCurrentUser } from './services/userService';
import { nativeNotificationService } from './services/nativeNotificationService';
import { offlineService } from './services/offlineService';
import { validateToken, clearAuthData } from './utils/auth';

function App() {
  const { isAuthenticated, isLoading } = useProtectedRoute();
  const { user, updateUser, logout } = useAuthStore();
  const theme = useSettingsStore(state => state.theme);
  const [checkingToken, setCheckingToken] = useState(false);

  // Sync effect
  useEffect(() => {
    const handleOnline = () => {
      offlineService.processQueue();
    };

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', () => {});

    // Initial check and sync
    if (navigator.onLine) {
      offlineService.processQueue();
    }

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', () => {});
    };
  }, []);

  // Register PWA service worker
  const {
    offlineReady: [offlineReady, setOfflineReady],
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegistered(r: any) {
      console.log('SW Registered: ' + r);
    },
    onRegisterError(error: any) {
      console.log('SW registration error', error);
    },
  });

  const close = () => {
    setOfflineReady(false);
    setNeedRefresh(false);
  };

  // Check token validity on mount
  useEffect(() => {
    const checkToken = async () => {
      const token = localStorage.getItem('accessToken');
      if (token) {
        try {
          const isValid = await validateToken(token);
          if (!isValid) {
            await clearAuthData();
            await logout();
          }
        } catch (error) {
          console.error('Token validation failed:', error);
          await clearAuthData();
          await logout();
        }
      }
      setCheckingToken(false);
    };
    
    checkToken();
  }, []);

  // Request Notification Permission on login
  useEffect(() => {
    if (isAuthenticated && !nativeNotificationService.isPermissionGranted()) {
      nativeNotificationService.requestPermission();
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [theme]);

  useEffect(() => {
    if (isAuthenticated && !user) {
      getCurrentUser()
        .then((userData) => {
          updateUser(userData);
        })
        .catch((error) => {
          console.error('Failed to load user profile', error);
        });
    }
  }, [isAuthenticated, user, updateUser]);

  if (isLoading || checkingToken) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>;
  }

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <>
      <ToastContainer position="bottom-right" />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/alerts" element={<ClinicalAlerts />} />
          <Route path="/analytics/trends" element={<BiomarkerTrends />} />
          <Route path="/analytics/correlative" element={<CorrelativeAnalytics />} />
          <Route path="/biomarkers" element={<BiomarkerTrends />} />
          <Route path="/biomarkers/catalog" element={<BiomarkerCatalog />} />
          <Route path="/biomarkers/details/:biomarkerId" element={<BiomarkerDetail />} />
          <Route path="/biomarkers/:categoryParam" element={<BiomarkerTrends />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/documents/:documentId" element={<DocumentDetail />} />
          <Route path="/examinations" element={<Examinations />} />
          <Route path="/examinations/categories" element={<ExaminationCategoryManager />} />
          <Route path="/examinations/upload" element={<ExaminationUpload />} />
          <Route path="/examinations/:examinationId" element={<ExaminationDetail />} />
          <Route path="/examinations/:examinationId/:activeTab" element={<ExaminationDetail />} />
          <Route path="/task-monitor" element={<TaskManager />} />
          <Route path="/patients" element={<Patients />} />
          <Route path="/patients/:patientId" element={<PatientDetail />} />
          <Route path="/patients/:patientId/:activeTab" element={<PatientDetail />} />
          <Route path="/events" element={<ClinicalEventList />} />
          <Route path="/events/:eventId" element={<ClinicalEventDetail />} />
          <Route path="/medications" element={<MedicationList />} />
          <Route path="/medications/catalog" element={<MedicationCatalog />} />
          <Route path="/medications/details/:medicationId" element={<MedicationDetail />} />
          <Route path="/notifications" element={<NotificationManagement />} />
          <Route path="/doctors" element={<Doctors />} />
          <Route path="/doctors/:doctorId" element={<DoctorDetail />} />
          <Route path="/organizations" element={<Organizations />} />
          <Route path="/organizations/:organizationId" element={<OrganizationDetail />} />
          
          {/* System Administration (System Admin Only) */}
          {user?.role === 'SYSTEM_ADMIN' && (
            <>
              <Route path="/admin/tenants" element={<TenantManagement />} />
              <Route path="/admin/system/ai-config" element={<AIConfig scope="global" />} />
              <Route path="/admin/system/catalogs" element={<CatalogManagement />} />
              <Route path="/admin/system/integrations" element={<SystemIntegrations />} />
            </>
          )}

          {/* Tenant Management (Admin & System Admin) */}
          {(user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN') && (
            <>
              <Route path="/admin/users" element={<UserManagement />} />
              <Route path="/admin/users/:userId" element={<UserDetail />} />
              <Route path="/admin/tenant/ai-config" element={<AIConfig scope="tenant" />} />
            </>
          )}

          <Route path="/telemetry" element={<Telemetry />} />
          <Route path="/ai-assistant" element={<AIChatPage />} />
          <Route path="/ai-assistant/:sessionId" element={<AIChatPage />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/profile" element={<Settings />} />
          <Route path="/settings/integrations" element={<Integrations />} />
          <Route path="/settings/integrations/:id" element={<IntegrationDetail />} />
          <Route path="/settings/ai-config" element={<AIConfig scope="user" />} />
          {(user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN') && (
            <Route path="/settings/export-import" element={<ExportImport />} />
          )}
          <Route path="/about" element={<AboutPage />} />
          <Route path="*" element={<Dashboard />} />
        </Route>
      </Routes>

      {/* PWA Update / Offline Toast */}
      {(offlineReady || needRefresh) && (
        <div className="fixed bottom-4 right-4 z-[9999] bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shadow-xl rounded-lg p-4 max-w-sm flex flex-col gap-2">
          <div className="flex justify-between items-start">
            <span className="text-sm font-medium text-slate-900 dark:text-white">
              {offlineReady ? 'App is ready for offline use' : 'A new version is available!'}
            </span>
            <button onClick={close} className="text-slate-400 hover:text-slate-500 transition-colors">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          {needRefresh && (
            <button
              onClick={() => updateServiceWorker(true)}
              className="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold py-2 px-3 rounded transition-colors"
            >
              Update now
            </button>
          )}
        </div>
      )}
    </>
  );
}

export default App;
