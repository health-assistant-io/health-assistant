import { useEffect, useState } from 'react';
import { useAuthStore } from '../store/slices/authSlice';

/**
 * §10 (plan 16 H3): there is no local token anymore — authentication is
 * the server-verified cookie session (see `initialize`). This hook only
 * surfaces the store state.
 */
export function useAuth() {
  const { user, isAuthenticated, login, logout, updateUser } = useAuthStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(false);
  }, []);

  return { user, isAuthenticated, login, logout, updateUser, loading };
}
