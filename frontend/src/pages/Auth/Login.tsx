import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import api from '../../api/axios';
import { validateToken, clearAuthData } from '../../utils/auth';
import AppVersion from '../../components/ui/AppVersion';

function Login() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { login, logout } = useAuthStore();
  const theme = useSettingsStore(state => state.theme);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check token validity + first-run status on mount. If the system is
  // uninitialized, redirect to the setup wizard instead of showing login.
  useEffect(() => {
    const init = async () => {
      const token = localStorage.getItem('accessToken');
      if (token) {
        const isValid = await validateToken(token);
        if (!isValid) {
          await clearAuthData();
          await logout();
        }
      }
      try {
        const res = await api.get('/auth/setup-status');
        if (res.data && !res.data.initialized) {
          navigate('/setup', { replace: true });
          return;
        }
      } catch {
        // Status endpoint unreachable — fall through to the login form.
      }
      setChecking(false);
    };

    init();
  }, [navigate, logout]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

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
        setError(t('auth.error_invalid_credentials'));
      }
    } catch (err) {
      const errorObj = err as Record<string, any>;
      console.error('Login failed:', errorObj);
      if (errorObj?.response?.status === 401) {
        setError(t('auth.error_invalid_credentials'));
      } else if (errorObj?.response?.status === 422) {
        setError(t('auth.error_invalid_format'));
      } else {
        setError(t('auth.error_server_down'));
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
          <img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-16 h-16 mx-auto mb-4" alt="Health Assistant Logo" />
          <h1 className="text-3xl font-bold text-blue-600">Health Assistant</h1>
          <p className="text-gray-600 dark:text-dark-muted mt-2">
            {t('auth.sign_in_title')}
          </p>
        </div>

        {error && (
          <div className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-lg flex items-start gap-3 text-red-700 dark:text-red-400 text-sm animate-in fade-in slide-in-from-top-2 duration-200">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5 shrink-0 mt-0.5">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
            </svg>
            <div>{error}</div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label
              htmlFor="email"
              className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2"
            >
              {t('auth.email_label')}
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setError(null);
              }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2"
            >
              {t('auth.password_label')}
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setError(null);
              }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="••••••••"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? t('auth.signing_in') : t('auth.sign_in_button')}
          </button>

          <div className="text-center">
            <p className="text-sm text-gray-500 dark:text-dark-muted">
              {t('auth.no_account', 'Need an account?')}
              <br />
              <span className="text-xs">
                {t('auth.invite_only_hint', 'Ask your administrator for an invite, or set up a new install.')}
              </span>
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