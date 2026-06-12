import { useEffect, useState } from 'react';
import { useAuthStore } from '../store/slices/authSlice';

export function useAuth() {
  const { user, token, isAuthenticated, login, logout, updateUser } = useAuthStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkAuth = async () => {
      const storedToken = localStorage.getItem('accessToken');
      if (storedToken) {
        // User is authenticated
      } else {
        logout();
      }
      setLoading(false);
    };

    checkAuth();
  }, []);

  return { user, token, isAuthenticated, login, logout, updateUser, loading };
}