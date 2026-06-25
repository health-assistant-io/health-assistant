import { useTranslation } from 'react-i18next';
import { Lock, Shield } from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';

function Security() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('settings.nav_security', 'Security')}
        subtitle={t('settings.section_security', 'Security')}
        icon={<Shield className="w-8 h-8" />}
      />

      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <Lock className="w-4 h-4 text-gray-400" />
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-dark-text">{t('settings.change_password', 'Change Password')}</p>
              <p className="text-sm text-gray-500 dark:text-dark-muted">
                {t('settings.change_password_desc', 'Update your account password')}
              </p>
            </div>
          </div>
          <button className="px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg hover:bg-gray-50 dark:hover:bg-dark-border">
            {t('common.edit', 'Change')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default Security;
