import { useTranslation } from 'react-i18next';
import { Building2 } from 'lucide-react';
import { SettingsPanel } from '../../components/settings/SettingsPanel';

const TenantSettingsPage: React.FC = () => {
  const { t } = useTranslation();
  return (
    <SettingsPanel
      level="tenant"
      title={t('settings.tenant_title', 'Tenant Settings')}
      subtitle={t('settings.tenant_subtitle', 'Defaults for everyone in this tenant. Users can override these.')}
      icon={<Building2 className="w-8 h-8" />}
    />
  );
};

export default TenantSettingsPage;
