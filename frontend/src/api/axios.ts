import axios, { type AxiosRequestConfig } from 'axios';
import { offlineService } from '../services/offlineService';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true, // §10 cookie sessions — the browser rides nx_access
  headers: {
    'Content-Type': 'application/json',
  },
});

// ---------------------------------------------------------------------------
// §10 cookie sessions (plan 16 H3): tokens NO LONGER live in localStorage —
// the HttpOnly `nx_access` / `nx_refresh` cookies are the browser credential
// and the JS-readable `nx_csrf` cookie fuels the double-submit echo. The
// JSON bodies from /auth/* still carry tokens for §9 user clients (Android,
// scripts); this client deliberately ignores them.
// ---------------------------------------------------------------------------

/** Fired when a request cannot be authenticated even after a refresh
 * attempt — listeners drop back to the login screen. */
export const UNAUTHENTICATED_EVENT = 'nx:unauthenticated';

/** Read the JS-readable CSRF cookie (double-submit — identity-auth §10). */
export function csrfToken(): string | null {
  const prefix = 'nx_csrf=';
  const match = document.cookie.split('; ').find((part) => part.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

/** True for methods the CSRF middleware gates (non-safe, §10). */
function isUnsafeMethod(method: string | undefined): boolean {
  return !['get', 'head', 'options'].includes((method || 'get').toLowerCase());
}

// One-time migration hygiene: upgraded browsers may still carry the pre-H3
// token keys. Purge them — THE LAW forbids token storage in Web Storage.
const LEGACY_TOKEN_KEYS = [
  'accessToken',
  'refreshToken',
  'originalAccessToken',
  'originalRefreshToken',
];
try {
  LEGACY_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key));
} catch {
  // storage unavailable (private mode) — nothing to purge
}

// Request interceptor
api.interceptors.request.use(
  async (config) => {
    // §10: cookie session — no Authorization header from storage. The
    // browser attaches the HttpOnly cookies (withCredentials above).

    // Double-submit CSRF (§10): echo the readable nx_csrf cookie on every
    // non-safe request. Always (re)set from the live cookie so a replayed
    // request after a refresh echoes the *rotated* value, not a stale one.
    if (isUnsafeMethod(config.method)) {
      const csrf = csrfToken();
      if (csrf) {
        config.headers['X-CSRF-Token'] = csrf;
      }
    }

    // Let the browser set the correct multipart Content-Type (with boundary)
    // when sending FormData — the default 'application/json' header would
    // otherwise override it and the server would reject the form fields.
    if (config.data instanceof FormData) {
      delete config.headers['Content-Type'];
      delete config.headers['content-type'];
    }

    // Check if offline for modification requests
    const isModification = ['post', 'put', 'patch', 'delete'].includes(config.method?.toLowerCase() || '');
    if (!navigator.onLine && isModification) {
      // Add to queue and pretend it's processing to avoid app crashing
      // We return a mock response that the frontend can handle
      await offlineService.addToQueue(config);
      return Promise.reject({
        message: 'OFFLINE_QUEUED',
        config
      });
    }

    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.message === 'OFFLINE_QUEUED') {
      // This is a special case we handled in the request interceptor
      return Promise.resolve({ data: { _offline: true, message: 'Queued for sync' }, status: 202 });
    }

    const originalRequest = error.config;

    // Check if it's a network error (potentially offline during request)
    if (!error.response && isNetworkError(error)) {
       const isModification = ['post', 'put', 'patch', 'delete'].includes(originalRequest.method?.toLowerCase() || '');
       if (isModification) {
          await offlineService.addToQueue(originalRequest);
          return Promise.resolve({ data: { _offline: true, message: 'Queued for sync' }, status: 202 });
       }
    }

    // Check if error is 401 and not already retried
    if (error.response?.status === 401 && !originalRequest._retry) {
      // Don't intercept 401s for auth endpoints to prevent infinite refresh
      // loops and let components handle the error. MUST include auth/refresh
      // — a failing refresh that retriggers the interceptor recursed forever
      // (audit 2026-08 FE-H4). auth/mfa covers the H5 login challenge: its
      // 401 means "second factor owed", not "session expired" — refreshing
      // would be wrong (there is no session yet) and would swallow the
      // challenge body the login page needs.
      const authUrls = ['auth/login', 'auth/register', 'auth/refresh', 'auth/demo-login', 'auth/setup', 'auth/mfa'];
      if (originalRequest.url && authUrls.some((u) => originalRequest.url!.includes(u))) {
        return Promise.reject(error);
      }

      originalRequest._retry = true;

      // §10: rotate the refresh cookie once (single-flight). No body — the
      // HttpOnly nx_refresh cookie (Path=/api/v1/auth) IS the credential.
      try {
        const refreshed = await refreshSession();
        if (!refreshed) {
          clearAuthData();
          window.location.href = '/login';
          return Promise.reject(error);
        }
        // The request interceptor re-runs on the replay and re-echoes the
        // (rotated) CSRF cookie.
        return api(originalRequest);
      } catch (refreshError) {
        // Refresh failed - the session is gone (expired family / revocation)
        clearAuthData();
        window.location.href = '/login';
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

function isNetworkError(error: any) {
  return error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED' || error.message === 'Network Error';
}

// Single-flight refresh (audit 2026-08 FE-H4): N concurrent 401s share one
// /auth/refresh call. Without the lock, parallel refreshes raced the token
// rotation server-side and cascaded into forced logouts.
//
// §10 (plan 16 H3): cookie-based — no refresh token in the body or storage;
// the server rotates the whole cookie triple. Returns true when a new
// session was established.
let refreshInFlight: Promise<boolean> | null = null;

export function refreshSession(): Promise<boolean> {
  if (refreshInFlight) {
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    try {
      // Bare axios (NOT the intercepted `api` instance) — going through the
      // interceptor would re-enter the 401 handler on refresh failure.
      const response = await axios.post(
        `${API_BASE_URL}/auth/refresh`,
        undefined,
        { withCredentials: true },
      );
      // Body tokens are for §9 clients; the browser ignores them.
      return response.status < 400;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

/**
 * Clears local authentication/session data (§10: the tokens themselves are
 * HttpOnly cookies — only the backend can clear those, via POST /auth/logout;
 * this sweeps the local leftovers). Called when the session is gone.
 */
function clearAuthData(): void {
  // Legacy pre-H3 token keys — defensive: nothing writes them anymore.
  LEGACY_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key));

  // Remove user data and session
  const authStore = localStorage.getItem('authStore');
  if (authStore) {
    const parsed = JSON.parse(authStore);
    const { user } = parsed;
    localStorage.setItem('authStore', JSON.stringify({ user }));
  }

  // Clear patient-related localStorage data
  const keysToRemove = [
    'selectedPatientId',
    'patientData',
    'patientLayout',
    'savedPatients',
    'activeExaminationId',
    'examinationData',
    'activeDocumentId',
    'documentData',
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
    'wearableData'
  ];

  keysToRemove.forEach(key => {
    localStorage.removeItem(key);
  });

  window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT));
}

export type { AxiosRequestConfig };
export default api;
