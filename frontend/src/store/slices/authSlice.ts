import { create } from 'zustand';
import { clearAuthData } from '../../utils/auth';

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
  token: string | null;
  refreshToken: string | null;
  isAuthenticated: boolean;
  isDemoMode: boolean;
  isLoading: boolean;
  login: (token: string, refreshToken: string) => void;
  setDemoMode: (demo: boolean) => void;
  logout: () => Promise<void>;
  updateUser: (user: User) => void;
  initialize: () => void;
}

// Check if token exists in localStorage
const getInitialAuth = () => {
  if (typeof window === 'undefined') return { token: null, refreshToken: null, isAuthenticated: false, isDemoMode: false };
  
  const token = localStorage.getItem('accessToken');
  const refreshToken = localStorage.getItem('refreshToken');
  
  // If no tokens, user is not authenticated
  if (!token) {
    return {
      token: null,
      refreshToken: null,
      isAuthenticated: false,
      isDemoMode: false
    };
  }
  
  // Validate token expiration
  const payload = validateToken(token);
  if (!payload || payload.exp < Date.now() / 1000) {
    // Token is expired, clear all auth data
    clearAuthData();
    return {
      token: null,
      refreshToken: null,
      isAuthenticated: false,
      isDemoMode: false
    };
  }
  
  return {
    token,
    refreshToken,
    isAuthenticated: true,
    isDemoMode: payload.demo === true
  };
};

/**
 * Validates JWT token payload
 */
/**
 * Validates JWT token payload
 */
function validateToken(token: string): any {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    
    const payload = JSON.parse(atob(parts[1]));
    return payload;
  } catch {
    return null;
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: getInitialAuth().token,
  refreshToken: getInitialAuth().refreshToken,
  isAuthenticated: getInitialAuth().isAuthenticated,
  isDemoMode: getInitialAuth().isDemoMode,
  isLoading: true,
  
  initialize: async () => {
    const { token, refreshToken, isAuthenticated, isDemoMode } = getInitialAuth();
    
    // If we have a token but it might be expired, check with server
    if (token && isAuthenticated) {
      try {
        const response = await fetch(`${import.meta.env.VITE_API_URL || '/api/v1'}/auth/validate`, {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        });
        
        if (!response.ok) {
          // Token is invalid or expired
          await clearAuthData();
          set({ token: null, refreshToken: null, isAuthenticated: false, isDemoMode: false, isLoading: false });
          return;
        }
      } catch (error) {
        // Network error or other issue, assume token is invalid
        await clearAuthData();
        set({ token: null, refreshToken: null, isAuthenticated: false, isDemoMode: false, isLoading: false });
        return;
      }
    }
    
    set({ token, refreshToken, isAuthenticated, isDemoMode, isLoading: false });
  },
  
  login: (token: string, refreshToken: string) => {
    localStorage.setItem('accessToken', token);
    localStorage.setItem('refreshToken', refreshToken);
    const payload = validateToken(token);
    set({
      token,
      refreshToken,
      isAuthenticated: true,
      isDemoMode: payload?.demo === true,
      isLoading: false
    });
  },

  setDemoMode: (demo: boolean) => set({ isDemoMode: demo }),
  
  logout: async () => {
    await clearAuthData();
    // Use dynamic import to avoid circular dependency
    const { usePatientStore } = await import('./patientSlice');
    usePatientStore.getState().clearPatientContext();
    
    set({
      user: null,
      token: null,
      refreshToken: null,
      isAuthenticated: false,
      isDemoMode: false,
      isLoading: false
    });
    // Redirect to login page to ensure clean state
    window.location.href = '/login';
  },
  
  updateUser: (user: User) => set((state) => ({
    user: state.user ? { ...state.user, ...user } : user
  }))
}));