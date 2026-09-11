import { Routes, Route, Navigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { lazy, Suspense } from 'react';
import { useRegisterSW } from 'virtual:pwa-register/react';
// Register the unified instance adapters once at app entry (side-effect import).
// Centralizing this here avoids circular imports that arise when a component
// in an adapter's transitive chain (e.g. ExaminationCard -> AssociatedEvents
// -> ExaminationEventModal) also imports the adapters barrel.
import './features/instances/adapters';
// Register the per-type browse views (reuses purpose-built components). Kept
// separate from the data adapters so adapter modules stay UI-free (no cycles).
import './features/instances/views';
// Register the per-type single-record detail views (reuses each entity's rich
// preview, e.g. ExaminationPreview) for the InstanceCard "open" overlay.
import './features/instances/details';
import Layout from './components/layout/Layout';
const RouteFallback = () => (
  <div className="flex h-[50vh] items-center justify-center text-sm text-gray-500 dark:text-gray-400">
    Loading...
  </div>
);

import Login from './pages/Auth/Login';
import Setup from './pages/Auth/Setup';
const RoleSetupWizard = lazy(() => import('./pages/Setup/RoleSetupWizard'));
const Dashboard = lazy(() => import('./pages/Dashboard/Dashboard'));
const AllergyList = lazy(() => import('./pages/Allergies/AllergyList'));
const AllergyDetail = lazy(() => import('./pages/Allergies/AllergyDetail'));
const BiomarkerTrends = lazy(() => import('./pages/Analytics').then(m => ({ default: m.BiomarkerTrends })));
const CorrelativeAnalytics = lazy(() => import('./pages/Analytics').then(m => ({ default: m.CorrelativeAnalytics })));
const Documents = lazy(() => import('./pages/Documents/DocumentList'));
const DocumentDetail = lazy(() => import('./pages/Documents/DocumentDetail'));
const AnatomyExplorer = lazy(() => import('./pages/Anatomy/AnatomyExplorer').then(m => ({ default: m.AnatomyExplorer })));
const Examinations = lazy(() => import('./pages/Examinations/ExaminationList'));
const ExaminationUpload = lazy(() => import('./pages/Examinations/ExaminationUpload'));
const ExaminationDetail = lazy(() => import('./pages/Examinations/ExaminationDetail'));
const ClinicalEventList = lazy(() => import('./pages/Events/ClinicalEventList'));
const ClinicalEventDetail = lazy(() => import('./pages/Events/ClinicalEventDetail'));
const TaskManager = lazy(() => import('./pages/TaskManager'));
const Patients = lazy(() => import('./pages/Patients/PatientList'));
const PatientDetail = lazy(() => import('./pages/Patients/PatientDetail'));
const PatientSetupWizard = lazy(() => import('./pages/Patients/PatientSetupWizard'));
const Doctors = lazy(() => import('./pages/Doctors/DoctorList'));
const MedicationList = lazy(() => import('./pages/Medications/MedicationList'));
const MedicationDetail = lazy(() => import('./pages/Medications/MedicationDetail'));
const CalendarPage = lazy(() => import('./pages/Calendar/CalendarPage'));
const NotificationManagement = lazy(() => import('./pages/Notifications/NotificationManagement'));
const BiomarkerDetail = lazy(() => import('./pages/Biomarkers/BiomarkerDetail'));
const AIChatPage = lazy(() => import('./pages/AI/AIChat'));
const DoctorDetail = lazy(() => import('./pages/Doctors/DoctorDetail'));
const Organizations = lazy(() => import('./pages/Organizations/OrganizationList'));
const OrganizationDetail = lazy(() => import('./pages/Organizations/OrganizationDetail'));
const AboutPage = lazy(() => import('./pages/About/AboutPage'));
const MyAccount = lazy(() => import('./pages/Account/MyAccount'));
const AppearanceSettings = lazy(() => import('./pages/Settings/AppearanceSettings'));
const Preferences = lazy(() => import('./pages/Settings/Preferences'));
const Security = lazy(() => import('./pages/Settings/Security'));
const TenantSettingsPage = lazy(() => import('./pages/Admin/TenantSettings'));
const SystemSettingsPage = lazy(() => import('./pages/Admin/SystemSettings'));
const Integrations = lazy(() => import('./pages/Settings/Integrations'));
const IntegrationDetail = lazy(() => import('./pages/Settings/IntegrationDetail'));
const OAuthConnected = lazy(() => import('./pages/Settings/OAuthConnected'));
const ExportImport = lazy(() => import('./pages/Settings/ExportImport'));
import SettingsShell from './components/settings/SettingsShell';
import {
  userSettingsNav,
  userSettingsHeader,
  tenantSettingsNav,
  tenantSettingsHeader,
  systemSettingsNav,
  systemSettingsHeader,
} from './config/settingsNav';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
const UserManagement = lazy(() => import('./pages/Admin/UserManagement'));
const UserDetail = lazy(() => import('./pages/Admin/UserDetail'));
const TenantManagement = lazy(() => import('./pages/Admin/TenantManagement'));
const TenantDetail = lazy(() => import('./pages/Admin/TenantDetail'));
const CatalogManagement = lazy(() => import('./pages/Admin/CatalogManagement'));
const CatalogWorkspace = lazy(() => import('./pages/Catalogs/CatalogWorkspace').then(m => ({ default: m.CatalogWorkspace })));
const VaccinationList = lazy(() => import('./pages/Vaccinations/VaccinationList').then(m => ({ default: m.VaccinationList })));
const SystemIntegrations = lazy(() => import('./pages/Admin/SystemIntegrations'));
const AtlasManager = lazy(() => import('./pages/Admin/AtlasManager'));
const OAuthClients = lazy(() => import('./pages/Admin/OAuthClients'));

const AIConfig = lazy(() => import('./pages/Settings/AIConfig').then(m => ({ default: m.AIConfig })));import { useProtectedRoute } from './hooks/useProtectedRoute';
import { useAuthStore } from './store/slices/authSlice';
import { useSettingsStore } from './store/slices/settingsSlice';
import { getCurrentUser } from './services/userService';
import { nativeNotificationService } from './services/nativeNotificationService';
import { offlineService } from './services/offlineService';
import { validateToken, clearAuthData } from './utils/auth';
import { useTenantSwitchStore } from './store/slices/tenantSwitchSlice';

function App() {
  const { isAuthenticated, isLoading } = useProtectedRoute();
  const { user, updateUser, logout } = useAuthStore();
  const theme = useSettingsStore(state => state.theme);
  const loadSettings = useSettingsStore(state => state.loadSettings);
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
    if (isAuthenticated) {
      import('./services/conceptService').then(({ loadDocumentCategories }) =>
        loadDocumentCategories(),
      );
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [theme]);

  // Sync tenant-switch state from the JWT on boot / token change.
  // The JWT's `switched` claim is the source of truth — the persisted
  // store can get out of sync if localStorage was partially cleared.
  const syncTenantSwitch = useTenantSwitchStore((s) => s.syncFromToken);
  useEffect(() => {
    if (isAuthenticated) {
      const token = localStorage.getItem('accessToken');
      if (token) {
        try {
          const payload = JSON.parse(atob(token.split('.')[1]));
          syncTenantSwitch(payload);
        } catch (e) {
          console.error('Failed to sync tenant switch state from JWT', e);
        }
      }
    }
  }, [isAuthenticated, syncTenantSwitch]);

  useEffect(() => {
    if (isAuthenticated && !user) {
      getCurrentUser()
        .then((userData) => {
          updateUser(userData);
        })
        .catch((error) => {
          console.error('Failed to load user profile', error);
          const token = localStorage.getItem('accessToken');
          if (token) {
            try {
              const payload = JSON.parse(atob(token.split('.')[1]));
              // Demo mode: a /users/me 404 means the session is stale — the
              // daily reset wiped the DB volume + re-seeded, so the user_id
              // in this JWT no longer exists. Don't mask it with the JWT
              // fallback; logout so /login auto-calls /auth/demo-login and
              // mints a fresh token against the re-seeded data.
              if (payload.demo === true) {
                logout();
                return;
              }
              // Non-demo fallback: decode the JWT to populate a minimal user
              // object so the app remains usable (e.g. during a tenant switch
              // where /users/me may transiently 404).
              updateUser({
                id: payload.user_id,
                email: payload.sub || '',
                role: payload.role,
                tenant_id: payload.tenant_id,
                settings: {},
              });
            } catch (e) {
              console.error('JWT fallback also failed', e);
            }
          }
        });
    }
  }, [isAuthenticated, user, updateUser, logout]);

  useEffect(() => {
    if (isAuthenticated) {
      loadSettings();
    }
  }, [isAuthenticated, loadSettings]);

  if (isLoading || checkingToken) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>;
  }

  if (!isAuthenticated) {
    return (
      <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/setup" element={<Setup />} />
        <Route path="*" element={<Login />} />
      </Routes>
      </Suspense>
    );
  }

  return (
    <>
      <ToastContainer position="bottom-right" />
      <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/setup/wizard" element={<RoleSetupWizard />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/alerts" element={<Navigate to="/allergies" replace />} />
          <Route path="/allergies" element={<AllergyList />} />
          <Route path="/allergies/details/:allergyId" element={<AllergyDetail />} />
          <Route path="/notifications" element={<NotificationManagement />} />
          <Route path="/notifications/:tab" element={<NotificationManagement />} />
          <Route path="/analytics/correlative" element={<CorrelativeAnalytics />} />
          <Route path="/biomarkers" element={<BiomarkerTrends />} />
          {/* Unified catalog workspace (Phase C) — replaces the per-type
              catalog pages. `/{type}/catalog` redirects to `/catalogs?type=`. */}
          <Route path="/catalogs" element={<CatalogWorkspace />} />
          <Route path="/biomarkers/catalog" element={<Navigate to="/catalogs?type=biomarker" replace />} />
          <Route path="/vaccines/catalog" element={<Navigate to="/catalogs?type=vaccine" replace />} />
          <Route path="/allergies/catalog" element={<Navigate to="/catalogs?type=allergy" replace />} />
          <Route path="/biomarkers/details/:biomarkerId" element={<BiomarkerDetail />} />
          <Route path="/biomarkers/details/:biomarkerId/:activeTab" element={<BiomarkerDetail />} />
          <Route path="/biomarkers/:categoryParam" element={<BiomarkerTrends />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/documents/:documentId" element={<DocumentDetail />} />
          <Route path="/examinations" element={<Examinations />} />
          <Route path="/examinations/categories" element={<Navigate to="/catalogs?type=concept" replace />} />
          <Route path="/examinations/upload" element={<ExaminationUpload />} />
          <Route path="/examinations/:examinationId" element={<ExaminationDetail />} />
          <Route path="/examinations/:examinationId/:activeTab" element={<ExaminationDetail />} />
          <Route path="/task-monitor" element={<TaskManager />} />
          <Route path="/patients" element={<Patients />} />
          <Route path="/patients/:patientId" element={<PatientDetail />} />
          <Route path="/patients/:patientId/setup" element={<PatientSetupWizard />} />
          <Route path="/patients/:patientId/:activeTab" element={<PatientDetail />} />
          <Route path="/events" element={<ClinicalEventList />} />
          <Route path="/events/:eventId" element={<ClinicalEventDetail />} />
          <Route path="/anatomy" element={<AnatomyExplorer />} />
          <Route path="/anatomy/:slug" element={<AnatomyExplorer />} />
          <Route path="/medications" element={<MedicationList />} />
          <Route path="/vaccinations" element={<VaccinationList />} />
          <Route path="/medications/catalog" element={<Navigate to="/catalogs?type=medication" replace />} />
          <Route path="/medications/details/:medicationId" element={<MedicationDetail />} />
          <Route path="/doctors" element={<Doctors />} />
          <Route path="/doctors/:doctorId" element={<DoctorDetail />} />
          <Route path="/organizations" element={<Organizations />} />
          <Route path="/organizations/:organizationId" element={<OrganizationDetail />} />
          
          {/* System Administration (System Admin Only) */}
          {user?.role === 'SYSTEM_ADMIN' && (
            <>
              {/* Full-page admin dashboards — no settings sidebar */}
              <Route path="/admin/system/tenants" element={<TenantManagement />} />
              <Route path="/admin/system/tenants/:tenantId" element={<TenantDetail />} />
              <Route path="/admin/system/users" element={<UserManagement />} />
              <Route path="/admin/system/users/:userId" element={<UserDetail />} />
              {/* Catalog workspace moved to /catalogs (all users, Phase C).
                  Stale admin bookmarks redirect there. */}
              <Route path="/admin/catalogs" element={<Navigate to="/catalogs" replace />} />

              {/* Configuration surfaces — share the settings shell */}
              <Route element={<SettingsShell nav={systemSettingsNav} header={systemSettingsHeader} />}>
                <Route path="/admin/system/settings" element={<SystemSettingsPage />} />
                <Route path="/admin/system/ai-config" element={<AIConfig scope="global" />} />
                <Route path="/admin/system/integrations" element={<SystemIntegrations />} />
                <Route path="/admin/system/catalogs" element={<CatalogManagement />} />
                <Route path="/admin/system/taxonomy" element={<Navigate to="/catalogs?type=concept" replace />} />
                <Route path="/admin/anatomy-atlas" element={<AtlasManager />} />
              </Route>
            </>
          )}

          {/* Tenant Management (Admin & System Admin) */}
          {(user?.role === 'ADMIN' || user?.role === 'MANAGER' || user?.role === 'SYSTEM_ADMIN') && (
            <>
              {/* Full-page admin dashboards — no settings sidebar */}
              <Route path="/admin/tenant/users" element={<UserManagement />} />
              <Route path="/admin/tenant/users/:userId" element={<UserDetail />} />
              <Route path="/admin/tenant/oauth-clients" element={<OAuthClients />} />

              {/* Configuration surfaces — share the settings shell */}
              <Route element={<SettingsShell nav={tenantSettingsNav} header={tenantSettingsHeader} />}>
                <Route path="/admin/tenant/settings" element={<TenantSettingsPage />} />
                <Route path="/admin/tenant/ai-config" element={<AIConfig scope="tenant" />} />
              </Route>
            </>
          )}


          <Route path="/ai-assistant" element={<AIChatPage />} />
          <Route path="/ai-assistant/:sessionId" element={<AIChatPage />} />
          <Route path="/profile" element={<MyAccount />} />
          <Route path="/settings" element={<SettingsShell nav={userSettingsNav} header={userSettingsHeader} />}>
            <Route index element={<Navigate to="/settings/appearance" replace />} />
            <Route path="preferences" element={<Preferences />} />
            <Route path="security" element={<Security />} />
            <Route path="appearance" element={<AppearanceSettings />} />
            <Route path="ai-config" element={<AIConfig scope="user" />} />
            <Route path="integrations" element={<Integrations />} />
            {(user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN') && (
              <Route path="export-import" element={<ExportImport />} />
            )}
          </Route>
          <Route path="/settings/integrations/:id" element={<IntegrationDetail />} />
          <Route path="/integrations/:domain/connected" element={<OAuthConnected />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="*" element={<Dashboard />} />
        </Route>
      </Routes>
      </Suspense>

      {/* PWA Update / Offline Toast */}
      {(offlineReady || needRefresh) && !window.__HA_SCREENSHOT_CAPTURE__ && (
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
