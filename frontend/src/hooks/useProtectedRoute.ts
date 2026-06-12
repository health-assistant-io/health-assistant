import { useEffect } from 'react';
import { useAuthStore } from '../store/slices/authSlice';
import { useNavigate } from 'react-router-dom';

export function useProtectedRoute() {
  const { isAuthenticated, isLoading, initialize } = useAuthStore();
  const navigate = useNavigate();

  useEffect(() => {
    // Initialize auth state from localStorage on mount
    if (isLoading) {
      initialize();
    }
  }, [isLoading, initialize]);

  useEffect(() => {
    // Only navigate after initialization is complete
    if (!isLoading) {
      if (!isAuthenticated) {
        navigate('/login', { replace: true });
      }
    }
  }, [isAuthenticated, isLoading, navigate]);

  return { isAuthenticated, isLoading };
}