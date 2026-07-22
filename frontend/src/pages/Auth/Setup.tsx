import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../store/slices/authSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import api from '../../api/axios';
import AppVersion from '../../components/ui/AppVersion';

interface SetupStatus {
  initialized: boolean;
  setup_token_required: boolean;
}

function Setup() {
  const navigate = useNavigate();
  const { login } = useAuthStore();
  const theme = useSettingsStore(state => state.theme);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [tenantName, setTenantName] = useState('');
  const [setupToken, setSetupToken] = useState('');
  const [tokenRequired, setTokenRequired] = useState(false);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // If the system is already initialized, there's nothing to set up —
  // bounce to login. This also guards against someone bookmarking /setup.
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const res = await api.get('/auth/setup-status');
        const status = res.data as SetupStatus;
        if (status.initialized) {
          navigate('/login', { replace: true });
          return;
        }
        setTokenRequired(!!status.setup_token_required);
      } catch {
        // If the status endpoint is unreachable, let the user try anyway —
        // the backend will return a precise error on submit.
      }
      setChecking(false);
    };
    checkStatus();
  }, [navigate]);

  const passwordsMatch = password === confirmPassword;
  const passwordLongEnough = password.length >= 8;
  const canSubmit =
    !!email && passwordLongEnough && passwordsMatch && !!tenantName && (!tokenRequired || !!setupToken);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!passwordsMatch) {
      setError('Passwords do not match.');
      return;
    }
    if (!passwordLongEnough) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setLoading(true);
    setError(null);

    try {
      const response = await api.post('/auth/setup', {
        email,
        password,
        tenant_name: tenantName,
        setup_token: tokenRequired ? setupToken : undefined,
      });

      if (response.data && response.data.access_token) {
        login(response.data.access_token, response.data.refresh_token);
        navigate('/dashboard', { replace: true });
      } else {
        setError('Setup did not return a session. Please try again.');
      }
    } catch (err) {
      const errorObj = err as Record<string, any>;
      const detail = errorObj?.response?.data?.detail;
      if (errorObj?.response?.status === 410) {
        // Already initialized — send to login.
        navigate('/login', { replace: true });
        return;
      }
      setError(detail || 'Setup failed. Check your details and try again.');
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
    <div className="flex items-center justify-center min-h-screen bg-gray-50 dark:bg-dark-bg px-4 py-8">
      <div className="max-w-md w-full bg-white dark:bg-dark-surface rounded-lg shadow-md p-8">
        <div className="text-center mb-8">
          <img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-16 h-16 mx-auto mb-4" alt="Health Assistant Logo" />
          <h1 className="text-3xl font-bold text-blue-600">Health Assistant</h1>
          <p className="text-gray-600 dark:text-dark-muted mt-2">
            Welcome — let's create your administrator account.
          </p>
        </div>

        <div className="mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/50 rounded-lg text-blue-700 dark:text-blue-300 text-sm">
          This is a fresh installation. The account you create here becomes the{' '}
          <strong>System Administrator</strong> for the whole instance.
        </div>

        {error && (
          <div className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-lg flex items-start gap-3 text-red-700 dark:text-red-400 text-sm animate-in fade-in slide-in-from-top-2 duration-200">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5 shrink-0 mt-0.5">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
            </svg>
            <div>{error}</div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label htmlFor="tenant_name" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              Organization / household name
            </label>
            <input
              id="tenant_name"
              type="text"
              required
              value={tenantName}
              onChange={(e) => { setTenantName(e.target.value); setError(null); }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="My Organization"
            />
          </div>

          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              Admin email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => { setEmail(e.target.value); setError(null); }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(null); }}
              className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text"
              placeholder="At least 8 characters"
            />
          </div>

          <div>
            <label htmlFor="confirm_password" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
              Confirm password
            </label>
            <input
              id="confirm_password"
              type="password"
              required
              value={confirmPassword}
              onChange={(e) => { setConfirmPassword(e.target.value); setError(null); }}
              className={`w-full px-4 py-2 border rounded-lg focus:ring-2 focus:border-transparent dark:bg-dark-border dark:text-dark-text ${
                confirmPassword && !passwordsMatch
                  ? 'border-red-400 focus:ring-red-500'
                  : 'border-gray-300 dark:border-dark-border focus:ring-blue-500'
              }`}
              placeholder="••••••••"
            />
            {confirmPassword && !passwordsMatch && (
              <p className="mt-1 text-xs text-red-500">Passwords do not match.</p>
            )}
          </div>

          {tokenRequired && (
            <div>
              <label htmlFor="setup_token" className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                Setup token
              </label>
              <input
                id="setup_token"
                type="text"
                required
                value={setupToken}
                onChange={(e) => { setSetupToken(e.target.value); setError(null); }}
                className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-dark-border dark:text-dark-text font-mono"
                placeholder="xxxx-xxxx"
              />
              <p className="mt-1 text-xs text-gray-500 dark:text-dark-muted">
                Find it in the backend container logs:{' '}
                <code className="text-xs">docker compose logs backend | grep -i "setup token"</code>
              </p>
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !canSubmit}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? 'Creating account…' : 'Create admin account'}
          </button>

          <div className="text-center border-t border-gray-100 dark:border-white/5 pt-4">
            <AppVersion />
          </div>
        </form>
      </div>
    </div>
  );
}

export default Setup;
