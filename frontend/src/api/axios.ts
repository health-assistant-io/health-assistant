import axios, { type AxiosRequestConfig } from 'axios';
import { offlineService } from '../services/offlineService';
import { notifyAccessDenied } from './accessDeniedToast';
import { stepUpChallengeOf } from '../services/stepUpTypes';
import { resolveStepUp } from '../components/auth/stepUpController';

export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true, // §10 cookie sessions — the browser rides ha_access
  headers: {
    'Content-Type': 'application/json',
  },
});

// ---------------------------------------------------------------------------
// §10 cookie sessions (plan 16 H3): tokens NO LONGER live in localStorage —
// the HttpOnly `ha_access` / `ha_refresh` cookies are the browser credential
// and the JS-readable `ha_csrf` cookie fuels the double-submit echo. The
// JSON bodies from /auth/* still carry tokens for §9 user clients (Android,
// scripts); this client deliberately ignores them.
// ---------------------------------------------------------------------------

/** Fired when a request cannot be authenticated even after a refresh
 * attempt — listeners drop back to the login screen. */
export const UNAUTHENTICATED_EVENT = 'nx:unauthenticated';

// ---------------------------------------------------------------------------
// Tenant security policy denials (ADR 0054 Phase 3): when the refresh fails
// with a typed policy reason, the reason must survive the hard redirect to
// /login so the login node can explain *why* the session ended (idle /
// max-age window, IP allowlist) instead of a bare sign-in form. sessionStorage
// — tab-local, cleared on consumption, never carries credentials.
// ---------------------------------------------------------------------------

const SESSION_END_KEY = 'nx_session_end';

/** The typed denial details the backend answers with (stable strings). */
export type SessionEndReason = 'session_idle_expired' | 'session_max_age_expired' | 'ip_not_allowed';

const SESSION_END_REASONS: readonly SessionEndReason[] = [
  'session_idle_expired',
  'session_max_age_expired',
  'ip_not_allowed',
];

/** Persist a typed policy denial for the login node to surface (best
 * effort — a blocked storage just means the generic message shows). */
export function storeSessionEndReason(reason: SessionEndReason): void {
  try {
    sessionStorage.setItem(SESSION_END_KEY, reason);
  } catch {
    // storage unavailable — the login page falls back to its generic copy
  }
}

/** Read + clear the stored reason (null when the session ended silently). */
export function consumeSessionEndReason(): SessionEndReason | null {
  try {
    const reason = sessionStorage.getItem(SESSION_END_KEY) as SessionEndReason | null;
    if (reason) sessionStorage.removeItem(SESSION_END_KEY);
    return reason && SESSION_END_REASONS.includes(reason) ? reason : null;
  } catch {
    return null;
  }
}

/** The typed policy denial carried by an auth error response (401 idle/
 * max-age, 403 ip_not_allowed) — null for any other failure. */
export function sessionEndReasonOf(error: unknown): SessionEndReason | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' && (SESSION_END_REASONS as readonly string[]).includes(detail)
    ? (detail as SessionEndReason)
    : null;
}

/** Read the JS-readable CSRF cookie (double-submit — identity-auth §10). */
export function csrfToken(): string | null {
  const prefix = 'ha_csrf=';
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

    // Double-submit CSRF (§10): echo the readable ha_csrf cookie on every
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

    // Step-up (ADR 0054 / program 12C1): a gated write answers 403 with a
    // typed body. Prompt for a fresh proof, then replay the original
    // request exactly once — never loop, and never intercept the step-up
    // ceremony itself. A user who cancels keeps the original error.
    const stepUp = stepUpChallengeOf(error);
    if (
      stepUp &&
      stepUp.code === 'step_up_required' &&
      originalRequest &&
      !originalRequest._stepUpRetry &&
      !isStepUpRequest(originalRequest.url)
    ) {
      const satisfied = await resolveStepUp(stepUp);
      if (satisfied) {
        originalRequest._stepUpRetry = true;
        return api(originalRequest);
      }
      return Promise.reject(error);
    }

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
      // HttpOnly ha_refresh cookie (Path=/api/v1/auth) IS the credential.
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

    // 403 (ADR 0054): only the central gate's denials surface as one clear
    // toast — they carry the `X-Authz-Denied` marker header (exposed to the
    // SPA via CORS `expose_headers`). Row-level denials (ownership checks)
    // answer with the same status but no marker: they are business answers
    // the caller handles, and toasting them would mislabel them as "your
    // role does not allow this action". Never retried, never redirected.
    if (error.response?.status === 403 && error.response?.headers?.['x-authz-denied']) {
      notifyAccessDenied();
    }

    return Promise.reject(error);
  }
);

function isNetworkError(error: any) {
  return error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED' || error.message === 'Network Error';
}

/** The step-up ceremony itself must never trigger another step-up prompt. */
function isStepUpRequest(url: string | undefined): boolean {
  return Boolean(url && url.includes('/auth/step-up'));
}

// Single-flight refresh (audit 2026-08 FE-H4): N concurrent 401s share one
// /auth/refresh call. Without the lock, parallel refreshes raced the token
// rotation server-side and cascaded into forced logouts.
//
// §10 (plan 16 H3): cookie-based — no refresh token in the body or storage;
// the server rotates the whole cookie triple. Returns true when a new
// session was established.
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Quiet session probe (bare axios, no interceptors): used by the SSO
 * popup poller while the callback tab holds the cookies. A false answer is
 * a normal intermediate state, never a logout trigger.
 */
export async function probeSession(): Promise<boolean> {
  try {
    const response = await axios.get(`${API_BASE_URL}/auth/validate`, {
      withCredentials: true,
    });
    return response.status < 400 && response.data?.valid !== false;
  } catch {
    return false;
  }
}

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
    } catch (refreshError) {
      // A typed policy denial (ADR 0054 Phase 3) is the *reason* the
      // session ended — persist it so /login can explain instead of a
      // bare sign-in form (no retry loop: the caller redirects once).
      const reason = sessionEndReasonOf(refreshError);
      if (reason) {
        storeSessionEndReason(reason);
      }
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
