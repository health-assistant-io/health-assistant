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
 * When an admin "enters" a tenant, the backend mints a scoped session whose
 * ``tenant_id`` is the target and whose ``original_tenant_id`` preserves
 * the admin's real tenant. §10 (plan 16 H3): the browser credential is the
 * HttpOnly cookie triple — the switch / exit-switch endpoints re-stamp it
 * server-side, so:
 *
 *   1. There are NO tokens to stash anymore (localStorage token storage is
 *      forbidden); the pre-H3 "save the originals" dance is gone.
 *   2. Exiting asks the backend to mint the restored session from the
 *      switched cookie's ``original_tenant_id`` claim. An expired switched
 *      access cookie is refreshed transparently by the axios interceptor
 *      (the switched claims survive refresh), so the old localStorage
 *      fallback is unnecessary; a hard failure falls back to re-login.
 *   3. This store tracks only UI state (banner + Exit button), persisted
 *      via zustand so a reload mid-switch keeps the banner.
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
  /** Sync the switched state from the session claims (call on app init). */
  syncFromToken: (claims: Record<string, any>) => void;
}

export const useTenantSwitchStore = create<TenantSwitchState>()(
  persist(
    (set, get) => ({
      switched: false,
      originalTenantId: null,
      scopedTenant: null,
      pendingRestore: false,

      enterTenant: (scopedTenant, originalTenantId) => {
        // §10: the backend's switch endpoint has already replaced the
        // cookie triple with the scoped session — nothing to save locally.
        set({
          switched: true,
          originalTenantId,
          scopedTenant,
          pendingRestore: false,
        });
      },

      exitTenant: async () => {
        // Ask the backend to mint a fresh restored session from the
        // switched cookie's original_tenant_id claim (the axios
        // interceptor refreshes an expired access cookie and retries).
        try {
          await apiExitSwitch();
        } catch (err) {
          // No local fallback exists in cookie mode (HttpOnly originals are
          // unreadable by design) — surface the failure to the caller and
          // let the UI offer a clean re-login.
          console.error('Tenant switch exit failed; re-login may be required', err);
          throw err;
        } finally {
          set({ switched: false, originalTenantId: null, scopedTenant: null, pendingRestore: false });
        }
      },

      clear: () => {
        set({ switched: false, originalTenantId: null, scopedTenant: null, pendingRestore: false });
      },

      syncFromToken: (claims) => {
        const tokenSwitched = claims.switched === true;
        const storeSwitched = get().switched;
        // If the session says switched but the store doesn't know (e.g.
        // after a page reload where the persisted store was cleared), sync
        // up. Claims come from GET /auth/validate (§10 — the JWT itself is
        // HttpOnly and undecodable from JS).
        if (tokenSwitched && !storeSwitched) {
          const scopedTenantId = claims.tenant_id || claims.scoped_tenant_id;
          set({
            switched: true,
            originalTenantId: claims.original_tenant_id ?? null,
            scopedTenant: scopedTenantId
              ? { id: scopedTenantId, name: 'Switched Tenant', slug: 'switched', is_active: true, settings: {} }
              : null,
            pendingRestore: false,
          });
        } else if (!tokenSwitched && storeSwitched) {
          // Session is not switched but the store thinks it is — clear
          // stale state.
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

/** Drive the full switch-into-tenant flow from one call site.
 *
 * §10: the backend re-stamps the cookie triple with the scoped session —
 * no tokens are handled (or storable) client-side anymore.
 */
export async function performTenantSwitch(tenantId: string): Promise<Tenant> {
  const result = await apiSwitchIntoTenant(tenantId);
  // Record the switch state for the banner / exit button.
  useTenantSwitchStore.getState().enterTenant(result.tenant, result.original_tenant_id);
  return result.tenant;
}
