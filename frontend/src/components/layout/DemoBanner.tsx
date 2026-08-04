import { useTranslation } from 'react-i18next';
import { FlaskConical, ExternalLink } from 'lucide-react';
import { useAuthStore } from '../../store/slices/authSlice';

const MAIN_SITE_URL = 'https://health-assistant.io';

/**
 * A fixed banner that appears at the top of the screen while the instance is
 * running in demo mode (DEMO_MODE=true). Pinned and non-dismissable — anyone
 * who is signed in got here through the credential-free /auth/demo-login
 * flow, so the banner must always make that clear. An "Exit demo" link on the
 * right opens the main product site so visitors can leave the sandbox.
 * Mirrors the TenantSwitchBanner pattern.
 */
function DemoBanner() {
  const { t } = useTranslation();
  const isDemoMode = useAuthStore((s) => s.isDemoMode);

  if (!isDemoMode) return null;

  return (
    <div className="bg-blue-600 text-white shadow-sm print:hidden shrink-0">
      <div className="max-w-7xl mx-auto px-4 py-1.5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 min-w-0 justify-center flex-1">
          <FlaskConical className="w-4 h-4 shrink-0" />
          <span className="text-xs sm:text-sm font-semibold truncate text-center">
            {t('common.demo_banner', 'Demo mode')}
            <span className="hidden sm:inline opacity-80 font-normal">
              {' '}{t('common.demo_banner_hint', '— data is synthetic and reset periodically. Do not store real health information.')}
            </span>
          </span>
        </div>
        <a
          href={MAIN_SITE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/15 hover:bg-white/25 transition-colors text-xs font-bold uppercase tracking-wide shrink-0"
          title={MAIN_SITE_URL}
        >
          <span className="hidden sm:inline">{t('common.demo_exit', 'Exit demo')}</span>
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
      </div>
    </div>
  );
}

export default DemoBanner;
