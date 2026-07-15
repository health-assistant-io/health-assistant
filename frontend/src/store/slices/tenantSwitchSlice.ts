import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import {
  switchIntoTenant as apiSwitchIntoTenant,
  exitTenantSwitch as apiExitSwitch,
  type Tenant,
} from '../../services/tenantService';

/**
 * Tenant-switch state for SYSTEM_ADMIN.
 *
 * When an admin "enters" a tenant, the backend mints a scoped JWT whose
 * ``tenant_id`` is the target and whose ``original_tenant_id`` preserves
 * the admin's real tenant. The frontend:
 *
 *   1. Saves the current (original) tokens to ``localStorage`` so they
 *      survive reloads and can be restored on exit.
 *   2. Replaces the active tokens with the scoped ones.
 *   3. Tracks the switched state here so the UI can show a banner and an
 *      Exit button.
 *
 * Persisted to ``localStorage`` so a page reload mid-switch doesn't lose
 * the original-session pointer (which would lock the admin out of their
 * real tenant until they logged out and back in).
 */
interface TenantSwitchState {
  switched: boolean;
  originalTenantId: string | null;
  scopedTenant: Tenant | null;
  /** Marker so the persisted store knows to attempt a restore on boot. */
  pendingRestore: boolean;

  enterTenant: (scopedTenant: Tenant, originalTenantId: string) => void;
  exitTenant: () => Promise<void>;
  clear: () => void;
  /** Sync the switched state from the JWT payload (call on app init). */
  syncFromToken: (payload: Record<string, any>) => void;
}

const ORIGINAL_TOKEN_KEY = 'originalAccessToken';
const ORIGINAL_REFRESH_KEY = 'originalRefreshToken';

export const useTenantSwitchStore = create<TenantSwitchState>()(
  persist(
    (set, get) => ({
      switched: false,
      originalTenantId: null,
      scopedTenant: null,
      pendingRestore: false,

      enterTenant: (scopedTenant, originalTenantId) => {
        const currentAccess = localStorage.getItem('accessToken');
        const currentRefresh = localStorage.getItem('refreshToken');
        if (currentAccess) localStorage.setItem(ORIGINAL_TOKEN_KEY, currentAccess);
        if (currentRefresh) localStorage.setItem(ORIGINAL_REFRESH_KEY, currentRefresh);
        set({
          switched: true,
          originalTenantId,
          scopedTenant,
          pendingRestore: false,
        });
      },

      exitTenant: async () => {
        // Ask the backend to mint a fresh restored token (uses the
        // switched token's original_tenant_id claim). Then restore the
        // original tokens to localStorage and clear the switched state.
        try {
          const result = await apiExitSwitch();
          localStorage.setItem('accessToken', result.access_token);
          localStorage.setItem('refreshToken', result.refresh_token);
        } catch (err) {
          // Fallback: restore the saved original tokens directly. This
          // covers the case where the switched token already expired.
          const originalAccess = localStorage.getItem(ORIGINAL_TOKEN_KEY);
          const originalRefresh = localStorage.getItem(ORIGINAL_REFRESH_KEY);
          if (originalAccess && originalRefresh) {
            localStorage.setItem('accessToken', originalAccess);
            localStorage.setItem('refreshToken', originalRefresh);
          }
          console.error('Tenant switch exit failed; restored originals from storage', err);
        } finally {
          localStorage.removeItem(ORIGINAL_TOKEN_KEY);
          localStorage.removeItem(ORIGINAL_REFRESH_KEY);
          set({ switched: false, originalTenantId: null, scopedTenant: null, pendingRestore: false });
        }
      },

      clear: () => {
        localStorage.removeItem(ORIGINAL_TOKEN_KEY);
        localStorage.removeItem(ORIGINAL_REFRESH_KEY);
        set({ switched: false, originalTenantId: null, scopedTenant: null, pendingRestore: false });
      },

      syncFromToken: (payload) => {
        const tokenSwitched = payload.switched === true;
        const storeSwitched = get().switched;
        // If the JWT says switched but the store doesn't know (e.g. after
        // a page reload where the persisted store was cleared), sync up.
        if (tokenSwitched && !storeSwitched) {
          const scopedTenantId = payload.tenant_id || payload.scoped_tenant_id;
          set({
            switched: true,
            originalTenantId: payload.original_tenant_id ?? null,
            scopedTenant: scopedTenantId
              ? { id: scopedTenantId, name: 'Switched Tenant', slug: 'switched', is_active: true, settings: {} }
              : null,
            pendingRestore: false,
          });
        } else if (!tokenSwitched && storeSwitched) {
          // JWT is not switched but store thinks it is — clear stale state.
          set({ switched: false, originalTenantId: null, scopedTenant: null, pendingRestore: false });
        }
      },
    }),
    {
      name: 'tenant-switch-storage',
      partialize: (state) => ({
        switched: state.switched,
        originalTenantId: state.originalTenantId,
        scopedTenant: state.scopedTenant,
        pendingRestore: state.pendingRestore,
      }),
    }
  )
);

/** Helper: drive the full switch-into-tenant flow from one call site. */
export async function performTenantSwitch(
  tenantId: string,
  onTokensUpdated: (accessToken: string, refreshToken: string) => void
): Promise<Tenant> {
  const result = await apiSwitchIntoTenant(tenantId);
  // Persist the new scoped tokens.
  localStorage.setItem('accessToken', result.access_token);
  localStorage.setItem('refreshToken', result.refresh_token);
  // Let the caller (usually authSlice.login) propagate to the store.
  onTokensUpdated(result.access_token, result.refresh_token);
  // Record the switch state.
  useTenantSwitchStore.getState().enterTenant(result.tenant, result.original_tenant_id);
  return result.tenant;
}

export { ORIGINAL_TOKEN_KEY, ORIGINAL_REFRESH_KEY };
