import { type ReactNode, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AuthGate } from '../ui/auth-gate';
import { UNAUTHENTICATED_EVENT } from '../../api/axios';
import { useAuthStore } from '../../store/slices/authSlice';

import Login from '../../pages/Auth/Login';

/**
 * Health's boot flow (identity-auth §4, plan 16 H3): the server-verified
 * cookie session is the credential — validate, with one cookie refresh
 * inside (`validateSession` via the auth store's `initialize`). There is
 * no desktop exchange and no local token to inspect (§10); the store is
 * synced here so claim-driven UI (tenant switch, demo flag) is populated
 * the moment the gate renders the app.
 */
async function bootGate(): Promise<boolean> {
  await useAuthStore.getState().initialize();
  return useAuthStore.getState().isAuthenticated;
}

/**
 * Composition root for the shared `AuthGate` (identity-auth §4, plan 16
 * Phase 5 closeout): the `checking → authenticated | anonymous` machine
 * lives in the library — health only wires the endpoints.
 *
 * - `bootGate` (cookie validate → refresh → re-validate, §10) is the
 *   machine input; a rejecting boot reads as anonymous — the gate never
 *   wedges on `checking`.
 * - A mid-session 401 that survives the axios refresh retry dispatches
 *   `UNAUTHENTICATED_EVENT` (after which the interceptor hard-reloads to
 *   /login — the reload re-runs this gate either way; the listener makes
 *   the recovery work even where the reload does not).
 * - A successful sign-in (password, MFA challenge, demo auto-login or the
 *   first-run setup wizard — all composed inside the `Login` node) bumps
 *   `resetKey` too; the replayed boot re-verifies the fresh cookie
 *   session and renders the app.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [epoch, setEpoch] = useState(0);
  const recheck = useCallback(() => setEpoch((e) => e + 1), []);

  // Mid-session 401 (failed refresh): re-run the machine through bootGate.
  useEffect(() => {
    window.addEventListener(UNAUTHENTICATED_EVENT, recheck);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, recheck);
  }, [recheck]);

  return (
    <AuthGate
      boot={bootGate}
      resetKey={epoch}
      login={<Login onSignedIn={recheck} />}
      checking={
        <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-dark-bg">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600" aria-label={t('auth.checking', 'Checking session…')} />
        </div>
      }
    >
      {children}
    </AuthGate>
  );
}
