import React, { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../store/slices/authSlice';
import api from '../../api/axios';
import { validateToken, clearAuthData } from '../../utils/auth';
import AppVersion from '../../components/ui/AppVersion';

function Login() {
  const navigate = useNavigate();
  const { login, logout } = useAuthStore();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);

  // Check if user has an expired token on mount
  useEffect(() => {
    const checkToken = async () => {
      const token = localStorage.getItem('accessToken');
      if (token) {
        const isValid = await validateToken(token);
        if (!isValid) {
          await clearAuthData();
          await logout();
        }
      }
      setChecking(false);
    };
    
    checkToken();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      // Use FormData for OAuth2 password grant
      const formData = new FormData();
      formData.append('username', email);
      formData.append('password', password);
      formData.append('grant_type', 'password');

      const response = await api.post('/auth/login', formData, {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
      });

      if (response.data && response.data.access_token) {
        // Update Zustand store (which also saves to localStorage)
        login(response.data.access_token, response.data.refresh_token);
        console.log('Login successful, redirecting to dashboard...');
        navigate('/dashboard', { replace: true });
      } else {
        alert('Invalid credentials');
      }
    } catch (err) {
      const error = err as Record<string, any>;
      console.error('Login failed:', error);
      if (error?.response?.status === 401) {
        alert('Invalid credentials. Please check your email and password.');
      } else if (error?.response?.status === 422) {
        alert('Invalid request format. Please try again.');
      } else {
        alert('Login failed. Please check if the server is running.');
      }
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-dark-bg">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-dark-bg">
      <div className="max-w-md w-full bg-white dark:bg-dark-surface rounded-lg shadow-md p-8">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-blue-600">Health Assistant</h1>
          <p className="text-gray-600 dark:text-dark-muted mt-2">
            Sign in to your account
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label
              htmlFor="email"
              className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2"
            >
              Email address
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="••••••••"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>

          <div className="text-center">
            <p className="text-sm text-gray-600 dark:text-dark-muted">
              Don't have an account?{' '}
              <Link to="/register" className="text-blue-600 hover:text-blue-700">
                Sign up
              </Link>
            </p>
          </div>

          <div className="text-center border-t border-gray-100 dark:border-white/5 pt-4">
            <AppVersion />
          </div>
        </form>
      </div>
    </div>
  );
}

export default Login;