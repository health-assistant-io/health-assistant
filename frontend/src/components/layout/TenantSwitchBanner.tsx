import { useNavigate } from 'react-router-dom';
import { ArrowLeft, AlertTriangle } from 'lucide-react';
import { useTenantSwitchStore } from '../../store/slices/tenantSwitchSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { usePatientStore } from '../../store/slices/patientSlice';

/**
 * A fixed banner that appears at the top of the screen when a SYSTEM_ADMIN
 * has switched into another tenant. Pinned and non-dismissable — the only
 * way to remove it is to exit the switch (which restores the original
 * session).
 *
 * The banner also makes the patient-context switcher reset, because the
 * scoped tenant has a different patient set.
 */
function TenantSwitchBanner() {
  const navigate = useNavigate();
  const { switched, scopedTenant, exitTenant } = useTenantSwitchStore();
  const login = useAuthStore((s) => s.login);
  const clearPatientContext = usePatientStore((s) => s.clearPatientContext);

  if (!switched) return null;

  const handleExit = async () => {
    // Snapshot the originals before exitTenant swaps the tokens.
    const originalAccess = localStorage.getItem('originalAccessToken');
    const originalRefresh = localStorage.getItem('originalRefreshToken');
    await exitTenant();
    if (originalAccess && originalRefresh) {
      // Refresh the auth store with the restored tokens (the user object
      // will be re-fetched on the next /auth/validate call).
      login(originalAccess, originalRefresh);
    }
    clearPatientContext();
    navigate('/admin/system/tenants', { replace: true });
  };

  return (
    <div className="bg-amber-500 text-white shadow-sm print:hidden shrink-0">
      <div className="max-w-7xl mx-auto px-4 py-2 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 min-w-0">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span className="text-sm font-semibold truncate">
            Viewing tenant <strong>{scopedTenant?.name ?? 'unknown'}</strong>
            <span className="hidden sm:inline opacity-80"> — exit to return to system admin view.</span>
          </span>
        </div>
        <button
          onClick={handleExit}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/15 hover:bg-white/25 transition-colors text-xs font-bold uppercase tracking-wide shrink-0"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Exit</span>
        </button>
      </div>
    </div>
  );
}

export default TenantSwitchBanner;
