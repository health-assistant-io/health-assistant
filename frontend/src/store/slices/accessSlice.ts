import { create } from 'zustand';
import api from '../../api/axios';

/**
 * The caller's authorization identity (ADR 0054 — `GET /me/access`).
 *
 * This store is a **mirror, never the gate**: the server enforces every
 * permission+scope centrally, and the UI only uses these answers to hide
 * and disable what the user cannot use (a `RequirePermission` guard
 * renders a clear no-access state instead of a 403 loop). Fail closed:
 * an unhydrated or failed fetch means "no permission anywhere".
 *
 * Hydrated after login, on session restore (`initialize`), and after a
 * tenant switch (the re-stamped session can carry a different role); a
 * role change bumps `token_version`, which kills the session — the next
 * sign-in hydrates the fresh set. Cleared on logout / dead sessions.
 * `patient_grants` are the caller's incoming care-team grants (plan D
 * Phase 2): `canPatient` ranks them read < write < admin as a UI *hint*
 * — per-resource enforcement stays on the server.
 */

export interface AccessTenant {
  id: string;
  name: string | null;
}

export interface PatientGrant {
  id: string;
  patient_id: string;
  patient_name?: string | null;
  permission: 'read' | 'write' | 'admin';
  source: 'admin' | 'care_team' | 'patient' | 'guardian';
  status: string;
  granted_at?: string | null;
  accepted_at?: string | null;
  expires_at?: string | null;
}

export interface MeAccessPayload {
  user: { id: string; email: string; full_name: string; role: string };
  roles: string[];
  permissions: string[];
  tenant: AccessTenant;
  patient_grants: PatientGrant[];
  pending_grants: PatientGrant[];
}

interface AccessState {
  permissions: Set<string>;
  roles: string[];
  tenant: AccessTenant | null;
  patientGrants: PatientGrant[];
  pendingGrants: PatientGrant[];
  /** True once the first `/me/access` for the live session resolved —
   * guards wait on this instead of flashing a no-access state. */
  isHydrated: boolean;
  /** The hydrate fetch failed (non-401) — everything stays denied. */
  hasError: boolean;
  hydrate: () => Promise<void>;
  clear: () => void;
  can: (permission: string) => boolean;
  canAny: (...permissions: string[]) => boolean;
  canPatient: (patientId: string, permission: string) => boolean;
}

const EMPTY_PERMISSIONS: ReadonlySet<string> = new Set();

const GRANT_RANK: Record<PatientGrant['permission'], number> = { read: 0, write: 1, admin: 2 };

let hydrateInFlight: Promise<void> | null = null;

export const useAccessStore = create<AccessState>((set, get) => ({
  permissions: EMPTY_PERMISSIONS as Set<string>,
  roles: [],
  tenant: null,
  patientGrants: [],
  pendingGrants: [],
  isHydrated: false,
  hasError: false,

  hydrate: async () => {
    if (hydrateInFlight) {
      return hydrateInFlight;
    }
    hydrateInFlight = (async () => {
      try {
        const response = await api.get<MeAccessPayload>('/me/access');
        const payload = response.data;
        set({
          permissions: new Set(payload.permissions ?? []),
          roles: payload.roles ?? [],
          tenant: payload.tenant ?? null,
          patientGrants: payload.patient_grants ?? [],
          pendingGrants: payload.pending_grants ?? [],
          isHydrated: true,
          hasError: false,
        });
      } catch {
        // Fail closed (401 rides the interceptor's refresh → /login path;
        // anything else — 403, network — leaves every permission denied).
        set({
          permissions: EMPTY_PERMISSIONS as Set<string>,
          roles: [],
          tenant: null,
          patientGrants: [],
          pendingGrants: [],
          isHydrated: true,
          hasError: true,
        });
      } finally {
        hydrateInFlight = null;
      }
    })();
    return hydrateInFlight;
  },

  clear: () => {
    set({
      permissions: EMPTY_PERMISSIONS as Set<string>,
      roles: [],
      tenant: null,
      patientGrants: [],
      pendingGrants: [],
      isHydrated: false,
      hasError: false,
    });
  },

  can: (permission: string) => get().permissions.has(permission),

  canAny: (...permissions: string[]) => {
    const held = get().permissions;
    return permissions.some((permission) => held.has(permission));
  },

  // Plan D Phase 2: rank the caller's incoming active grants for this
  // patient (read < write < admin). Ownership/staff reach is *not* known
  // here — those callers are gated by role elsewhere; the server owns
  // per-resource enforcement regardless, so callers use this to gate UI
  // hints only. Fail closed: settled grants (revoked/expired/declined) and
  // grants past their `expires_at` never count, even in a stale mirror.
  canPatient: (patientId: string, permission: string) => {
    const needed = GRANT_RANK[permission as PatientGrant['permission']];
    if (needed === undefined) return false;
    const now = Date.now();
    return get().patientGrants.some((grant) => {
      if (grant.patient_id !== patientId) return false;
      if (grant.status !== 'active') return false;
      if (grant.expires_at && new Date(grant.expires_at).getTime() <= now) return false;
      return GRANT_RANK[grant.permission] >= needed;
    });
  },
}));
