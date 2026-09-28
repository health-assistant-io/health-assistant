/**
 * Session validation against the backend (§10 cookie mode, plan 16 H3).
 *
 * The browser credential is the HttpOnly `nx_access` cookie — there is no
 * token to inspect locally. This asks the server (cookie rides along via
 * credentials: 'include') and, on a 401, tries one cookie-based refresh
 * before giving up. Body tokens from /auth/* are ignored on purpose.
 */
import { OFFLINE_DB_NAME } from '../services/db';

const API_BASE_URL = () => import.meta.env.VITE_API_URL || '/api/v1';

export interface SessionClaims {
  valid: boolean;
  user_id: string;
  email?: string | null;
  role?: string | null;
  tenant_id?: string | null;
  auth_mode?: string | null;
  switched?: boolean;
  original_tenant_id?: string | null;
}

/**
 * Validates the cookie session with the backend; one refresh attempt on 401.
 * Returns the session claims (tenant/role/auth_mode/switched) — the frontend
 * cannot decode the HttpOnly JWT, so this is the source for claim-derived
 * UI state (e.g. the tenant-switch banner).
 */
export async function validateSession(): Promise<SessionClaims | null> {
  const base = API_BASE_URL();
  try {
    let response = await fetch(`${base}/auth/validate`, { credentials: 'include' });
    if (response.status === 401) {
      // Expired access cookie with a live refresh cookie: rotate once.
      const refreshed = await fetch(`${base}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });
      if (refreshed.ok) {
        response = await fetch(`${base}/auth/validate`, { credentials: 'include' });
      }
    }
    if (!response.ok) {
      return null;
    }
    const data = (await response.json()) as SessionClaims;
    return data.valid === true ? data : null;
  } catch {
    return null;
  }
}

/**
 * Clears all authentication and session data from localStorage, cookies, IndexedDB, and Cache API
 */
export async function clearAuthData(): Promise<void> {
  // 1. Clear LocalStorage related to session
  const keysToRemove = [
    // Legacy pre-H3 token keys (§10 forbids token storage — nothing writes
    // these anymore; the sweep is defensive for upgraded browsers).
    'accessToken',
    'refreshToken',
    // Tenant-switch originals (audit 2026-08 FE-M2): the old case-sensitive
    // substring sweep ('originalAccessToken'.includes('auth') === false)
    // left a SYSTEM_ADMIN's real tokens on disk after logout.
    'originalAccessToken',
    'originalRefreshToken',
    'authStore', // Usually contains user/session info
    'user',
    'selectedPatientId',
    'patientData',
    'patientLayout',
    'savedPatients',
    'activeExaminationId',
    'examinationData',
    'activeDocumentId',
    'documentData',
    // The dead documentSlice (localStorage['documents'], audit 2026-08
    // FE-M5) was deleted as part of the 2026-09-11 audit FE-1 — PHI must
    // never persist here; the wipe list stays defensive for history.
    'documents',
    'recentDocuments',
    'activeBiomarkerId',
    'biomarkerData',
    'dashboardConfig',
    'activeMedicationId',
    'medicationData',
    'activeAllergyId',
    'allergyData',
    'activeDoctorId',
    'doctorData',
    'wearableData',
    // Persisted AI/tenant configs may carry key-shaped fields (FE-L4).
    'ai-config-storage',
    'settings-storage',
  ];

  keysToRemove.forEach(key => {
    localStorage.removeItem(key);
  });

  // Also clear any other prefixed keys if used. Case-insensitive matching
  // on 'auth'/'token' catches 'originalAccessToken' variants (audit
  // 2026-08 FE-M2).
  Object.keys(localStorage).forEach(key => {
    const lower = key.toLowerCase();
    if (
      lower.includes('patient') ||
      lower.includes('examination') ||
      lower.includes('auth') ||
      lower.includes('token')
    ) {
      localStorage.removeItem(key);
    }
  });

  // 2. Clear SessionStorage
  sessionStorage.clear();

  // 3. Clear all Cookies
  const cookies = document.cookie.split(";");
  for (let i = 0; i < cookies.length; i++) {
    const cookie = cookies[i];
    const eqPos = cookie.indexOf("=");
    const name = eqPos > -1 ? cookie.substr(0, eqPos).trim() : cookie.trim();
    // Try to remove from common paths and domains
    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/`;
    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=${window.location.hostname}`;
  }

  // 4. Clear Cache API (Service Worker caches)
  if ('caches' in window) {
    try {
      const names = await caches.keys();
      await Promise.all(names.map(name => caches.delete(name)));
    } catch (e) {
      console.error("Failed to clear cache:", e);
    }
  }

  // 5. Clear IndexedDB
  if (window.indexedDB) {
    // Explicitly delete our known DBs. The offline cache name is imported
    // from db.ts so a rename can't silently resurrect a stale copy (audit D1:
    // the hardcoded list used an underscore where the DB opens a hyphen).
    const dbNames = [OFFLINE_DB_NAME, 'health-assistant-cache', 'workbox-precache-v2'];
    dbNames.forEach(dbName => {
      try {
        window.indexedDB.deleteDatabase(dbName);
      } catch (e) {
        console.error(`Failed to delete IndexedDB ${dbName}:`, e);
      }
    });

    // Attempt to delete all databases if supported
    if (window.indexedDB.databases) {
      try {
        const dbs = await window.indexedDB.databases();
        await Promise.all(dbs.map(db => {
          if (db.name) return window.indexedDB.deleteDatabase(db.name);
          return Promise.resolve();
        }));
      } catch (e) {
        console.error("Failed to clear all IndexedDBs:", e);
      }
    }
  }

  // 6. Unregister all Service Workers
  if ('serviceWorker' in navigator) {
    try {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map(r => r.unregister()));
    } catch (e) {
      console.error("Failed to unregister Service Worker:", e);
    }
  }
}