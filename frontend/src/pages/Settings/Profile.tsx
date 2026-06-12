import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useNavigate } from 'react-router-dom';
import { AIConfig } from './AIConfig';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { Settings as SettingsIcon } from 'lucide-react';

function Settings() {
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const { language, setLanguage, notificationsEnabled, setNotificationsEnabled, unitSystem, setUnitSystem } = useSettingsStore();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        subtitle="Manage your profile and application preferences"
        icon={<SettingsIcon className="w-8 h-8" />}
      />

      <StickyToolbar
        actions={
          <button
            onClick={() => navigate('/settings/ai-config')}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95"
          >
            Configure AI Providers
          </button>
        }
      />

      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6 space-y-6">
        {/* Profile Section */}
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-dark-text mb-4">
            Profile Information
          </h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                Email
              </label>
              <input
                type="email"
                value={user?.email || ''}
                disabled
                className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg bg-gray-50 dark:bg-dark-border dark:text-dark-muted"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                Role
              </label>
              <input
                type="text"
                value={user?.role || ''}
                disabled
                className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg bg-gray-50 dark:bg-dark-border dark:text-dark-muted"
              />
            </div>
          </div>
        </div>

        {/* AI Configuration Section */}
        <div className="pt-6 border-t border-gray-100 dark:border-dark-border">
          <AIConfig scope="user" embedded={true} />
        </div>

        {/* Preferences Section */}
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-dark-text mb-4">
            Preferences
          </h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-dark-text">Language</p>
                <p className="text-sm text-gray-500 dark:text-dark-muted">
                  {language === 'en' ? 'English' : 'Other'}
                </p>
              </div>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              >
                <option value="en">English</option>
                <option value="es">Spanish</option>
                <option value="fr">French</option>
              </select>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-dark-text">Unit System</p>
                <p className="text-sm text-gray-500 dark:text-dark-muted">
                  {unitSystem === 'metric' ? 'Metric (kg, cm)' : 'Imperial (lbs, ft)'}
                </p>
              </div>
              <select
                value={unitSystem}
                onChange={(e) => setUnitSystem(e.target.value as 'metric' | 'imperial')}
                className="px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
              >
                <option value="metric">Metric</option>
                <option value="imperial">Imperial</option>
              </select>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-dark-text">Notifications</p>
                <div className="flex flex-col">
                  <p className="text-sm text-gray-500 dark:text-dark-muted">
                    {notificationsEnabled ? 'Enabled' : 'Disabled'}
                  </p>
                  <p className="text-xs text-blue-500 cursor-pointer hover:underline" onClick={async () => {
                    const { nativeNotificationService } = await import('../../services/nativeNotificationService');
                    const sub = await nativeNotificationService.subscribeToPush();
                    if (sub) {
                      setNotificationsEnabled(true);
                      alert(`Successfully subscribed to push notifications!`);
                    } else {
                      alert(`Failed to subscribe. Please check browser permissions.`);
                    }
                  }}>
                    Configure browser permissions
                  </p>
                </div>
              </div>
              <button
                onClick={async () => {
                  if (!notificationsEnabled) {
                    const { nativeNotificationService } = await import('../../services/nativeNotificationService');
                    const sub = await nativeNotificationService.subscribeToPush();
                    if (sub) setNotificationsEnabled(true);
                  } else {
                    setNotificationsEnabled(false);
                  }
                }}
                className={`px-4 py-2 rounded-lg ${
                  notificationsEnabled
                    ? 'bg-green-600 text-white'
                    : 'bg-gray-200 dark:bg-dark-border text-gray-600 dark:text-dark-muted'
                }`}
              >
                {notificationsEnabled ? 'Enabled' : 'Disabled'}
              </button>
            </div>
          </div>
        </div>

        {/* Security Section */}
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-dark-text mb-4">
            Security
          </h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-dark-text">Change Password</p>
                <p className="text-sm text-gray-500 dark:text-dark-muted">
                  Update your account password
                </p>
              </div>
              <button className="px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg hover:bg-gray-50 dark:hover:bg-dark-border">
                Change
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Settings;
