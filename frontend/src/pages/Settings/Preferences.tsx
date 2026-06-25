import { useTranslation } from 'react-i18next';
import { Globe, Ruler, Bell, SlidersHorizontal } from 'lucide-react';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { PageHeader } from '../../components/ui/PageHeader';

function Preferences() {
  const { t } = useTranslation();
  const { language, setLanguage, notificationsEnabled, setNotificationsEnabled, unitSystem, setUnitSystem } = useSettingsStore();

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('settings.nav_preferences', 'Preferences')}
        subtitle={t('settings.section_preferences', 'Preferences')}
        icon={<SlidersHorizontal className="w-8 h-8" />}
      />

      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <Globe className="w-4 h-4 text-gray-400" />
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-dark-text">{t('settings.language', 'Language')}</p>
              <p className="text-sm text-gray-500 dark:text-dark-muted">
                {language === 'en' ? t('settings.language_en', 'English') : t('settings.language_el', 'Greek')}
              </p>
            </div>
          </div>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
          >
            <option value="en">{t('settings.language_en', 'English')}</option>
            <option value="el">{t('settings.language_el', 'Greek')}</option>
          </select>
        </div>

        <div className="flex items-center justify-between pt-6 border-t border-gray-100 dark:border-dark-border">
          <div className="flex items-center space-x-3">
            <Ruler className="w-4 h-4 text-gray-400" />
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-dark-text">{t('settings.unit_system', 'Unit System')}</p>
              <p className="text-sm text-gray-500 dark:text-dark-muted">
                {unitSystem === 'metric' ? t('settings.unit_system_metric', 'Metric (kg, cm)') : t('settings.unit_system_imperial', 'Imperial (lbs, ft)')}
              </p>
            </div>
          </div>
          <select
            value={unitSystem}
            onChange={(e) => setUnitSystem(e.target.value as 'metric' | 'imperial')}
            className="px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg dark:bg-dark-border dark:text-dark-text"
          >
            <option value="metric">{t('settings.unit_system_metric', 'Metric')}</option>
            <option value="imperial">{t('settings.unit_system_imperial', 'Imperial')}</option>
          </select>
        </div>

        <div className="flex items-center justify-between pt-6 border-t border-gray-100 dark:border-dark-border">
          <div className="flex items-center space-x-3">
            <Bell className="w-4 h-4 text-gray-400" />
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-dark-text">{t('settings.notifications_enabled', 'Notifications')}</p>
              <p className="text-sm text-gray-500 dark:text-dark-muted">
                {notificationsEnabled ? t('admin.active', 'Enabled') : t('common.inactive', 'Disabled')}
              </p>
              <p className="text-xs text-blue-500 cursor-pointer hover:underline" onClick={async () => {
                const { nativeNotificationService } = await import('../../services/nativeNotificationService');
                const sub = await nativeNotificationService.subscribeToPush();
                if (sub) {
                  setNotificationsEnabled(true);
                  alert(t('settings.push_subscribed', 'Successfully subscribed to push notifications!'));
                } else {
                  alert(t('settings.push_failed', 'Failed to subscribe. Please check browser permissions.'));
                }
              }}>
                {t('settings.configure_browser_permissions', 'Configure browser permissions')}
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
            {notificationsEnabled ? t('admin.active', 'Enabled') : t('common.inactive', 'Disabled')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default Preferences;
