import { create } from 'zustand';
import api from '../../api/axios';
import { clearAuthData, validateSession, type SessionClaims } from '../../utils/auth';

export type UserRole = 'SYSTEM_ADMIN' | 'ADMIN' | 'MANAGER' | 'USER';

interface User {
  id: string;
  email: string;
  role: UserRole;
  tenant_id?: string;
  settings: {
    preferred_units?: {
      weight: string;
      height: string;
      glucose: string;
    };
    ai_config?: {
      ocr?: {
        provider?: string;
        api_key?: string;
        api_base?: string;
        model?: string;
      };
      nlp?: {
        provider?: string;
        api_key?: string;
        api_base?: string;
        model?: string;
      };
    };
  };
}

interface AuthState {
  user: User | null;
  /** §10 (plan 16 H3): server-verified session claims — the frontend can
   * no longer decode the HttpOnly JWT, so tenant/role/auth_mode/switched
   * state comes from GET /auth/validate via `initialize`. */
  claims: SessionClaims | null;
  isAuthenticated: boolean;
  isDemoMode: boolean;
  isLoading: boolean;
  login: () => void;
  setDemoMode: (demo: boolean) => void;
  /** Local-only state reset (no server call, no redirect) — for a session
   * discovered dead during boot checks. */
  resetSession: () => void;
  logout: () => Promise<void>;
  updateUser: (user: User) => void;
  initialize: () => Promise<void>;
}

// §10: NO token storage. The browser credential is the HttpOnly cookie
// triple set by the backend (nx_access / nx_refresh / nx_csrf); this store
// only tracks server-verified booleans + claims. localStorage/sessionStorage
// tokens are forbidden for browser clients (identity-auth §10).
export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  claims: null,
  isAuthenticated: false,
  isDemoMode: false,
  isLoading: true,

  initialize: async () => {
    // Ask the server whether the cookie session is alive (one refresh
    // attempt is included). Claims ride along for the tenant-switch UI.
    const claims = await validateSession();
    if (!claims) {
      set({ claims: null, isAuthenticated: false, isDemoMode: false, isLoading: false });
      return;
    }
    set({
      claims,
      isAuthenticated: true,
      isDemoMode: claims.auth_mode === 'demo',
      isLoading: false,
    });
  },

  login: () => {
    // §10: the login/setup/demo-login responses set the cookie triple;
    // the body tokens are for §9 user clients and are ignored here.
    set({
      isAuthenticated: true,
      isLoading: false,
    });
    // Re-sync claim-derived state (demo flag, switched session) from the
    // freshly-minted cookie session.
    validateSession().then((claims) => {
      if (claims) {
        set({ claims, isDemoMode: claims.auth_mode === 'demo' });
      }
    });
  },

  setDemoMode: (demo: boolean) => set({ isDemoMode: demo }),

  resetSession: () => {
    // Local-only reset (§10): for boot checks that find the cookie session
    // dead — no server call, no redirect (calling `logout` here would
    // bounce to /login, remount, re-check and redirect forever).
    set({
      user: null,
      claims: null,
      isAuthenticated: false,
      isDemoMode: false,
      isLoading: false,
    });
  },

  logout: async () => {
    // §10: the cookies are HttpOnly — only the backend can clear them.
    // Best-effort revocation (CSRF header auto-attached by the interceptor;
    // an expired access cookie is refreshed-and-retried transparently).
    try {
      await api.post('/auth/logout');
    } catch {
      // Session already gone / network down — local cleanup still runs.
    }
    await clearAuthData();
    // Use dynamic import to avoid circular dependency
    const { usePatientStore } = await import('./patientSlice');
    usePatientStore.getState().clearPatientContext();

    set({
      user: null,
      claims: null,
      isAuthenticated: false,
      isDemoMode: false,
      isLoading: false,
    });
    // Redirect to login page to ensure clean state
    window.location.href = '/login';
  },

  updateUser: (user: User) => set((state) => ({
    user: state.user ? { ...state.user, ...user } : user
  }))
}));
