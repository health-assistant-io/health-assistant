import { useTranslation } from 'react-i18next';
import { FlaskConical } from 'lucide-react';
import { useAuthStore } from '../../store/slices/authSlice';

/**
 * A fixed banner that appears at the top of the screen while the instance is
 * running in demo mode (DEMO_MODE=true). Pinned and non-dismissable — anyone
 * who is signed in got here through the credential-free /auth/demo-login
 * flow, so the banner must always make that clear. Mirrors the
 * TenantSwitchBanner pattern.
 */
function DemoBanner() {
  const { t } = useTranslation();
  const isDemoMode = useAuthStore((s) => s.isDemoMode);

  if (!isDemoMode) return null;

  return (
    <div className="bg-blue-600 text-white shadow-sm print:hidden shrink-0">
      <div className="max-w-7xl mx-auto px-4 py-1.5 flex items-center justify-center gap-2">
        <FlaskConical className="w-4 h-4 shrink-0" />
        <span className="text-xs sm:text-sm font-semibold truncate text-center">
          {t('common.demo_banner', 'Demo mode')}
          <span className="hidden sm:inline opacity-80 font-normal">
            {' '}{t('common.demo_banner_hint', '— data is synthetic and reset periodically. Do not store real health information.')}
          </span>
        </span>
      </div>
    </div>
  );
}

export default DemoBanner;
