import { useTranslation } from 'react-i18next';
import { Globe } from 'lucide-react';
import { SettingsPanel } from '../../components/settings/SettingsPanel';

const SystemSettingsPage: React.FC = () => {
  const { t } = useTranslation();
  return (
    <SettingsPanel
      level="system"
      title={t('settings.system_title', 'System Settings')}
      subtitle={t('settings.system_subtitle', 'Global defaults for all tenants. Tenant and user settings override these.')}
      icon={<Globe className="w-8 h-8" />}
    />
  );
};

export default SystemSettingsPage;
