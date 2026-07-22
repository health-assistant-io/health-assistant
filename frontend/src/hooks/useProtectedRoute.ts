import { useEffect } from 'react';
import { useAuthStore } from '../store/slices/authSlice';
import { useNavigate, useLocation } from 'react-router-dom';

// Auth pages that handle their own redirects (e.g. login → setup wizard).
// useProtectedRoute must not yank the user off these when unauthenticated,
// or it creates a redirect ping-pong (setup → login → setup → …).
const AUTH_PATHS = ['/login', '/setup'];

export function useProtectedRoute() {
  const { isAuthenticated, isLoading, initialize } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    // Initialize auth state from localStorage on mount
    if (isLoading) {
      initialize();
    }
  }, [isLoading, initialize]);

  useEffect(() => {
    // Only navigate after initialization is complete
    if (!isLoading && !isAuthenticated) {
      // Skip the forced /login redirect when already on an auth page —
      // those pages decide between login and the first-run setup wizard.
      if (!AUTH_PATHS.includes(location.pathname)) {
        navigate('/login', { replace: true });
      }
    }
  }, [isAuthenticated, isLoading, navigate, location.pathname]);

  return { isAuthenticated, isLoading };
}