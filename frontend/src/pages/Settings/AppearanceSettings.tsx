import { useTranslation } from 'react-i18next';
import { Palette } from 'lucide-react';
import { SettingsPanel } from '../../components/settings/SettingsPanel';

const UserSettingsPage: React.FC = () => {
  const { t } = useTranslation();
  return (
    <SettingsPanel
      level="user"
      title={t('settings.appearance_title', 'My Settings')}
      subtitle={t('settings.appearance_subtitle', 'Personal preferences. Override the defaults set by your organization.')}
      icon={<Palette className="w-8 h-8" />}
    />
  );
};

export default UserSettingsPage;
