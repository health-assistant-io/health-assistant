import axios from 'axios';
import { offlineService } from '../services/offlineService';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor
api.interceptors.request.use(
  async (config) => {
    const token = localStorage.getItem('accessToken');
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`;
    } else if (!['auth/login', 'auth/register', 'auth/refresh'].some(u => config.url?.includes(u))) {
      console.warn('No access token found for request:', config.url);
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
      // Don't intercept 401s for auth endpoints to prevent infinite refresh loops and let components handle the error
      if (originalRequest.url?.includes('auth/login') || originalRequest.url?.includes('auth/register')) {
        return Promise.reject(error);
      }

      originalRequest._retry = true;
      
      const refreshToken = localStorage.getItem('refreshToken');
      
      // If no refresh token, immediately redirect to login
      if (!refreshToken) {
        clearAuthData();
        window.location.href = '/login';
        return Promise.reject(error);
      }
      
      try {
        const newToken = await refreshAccessToken();
        api.defaults.headers.Authorization = `Bearer ${newToken}`;
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return api(originalRequest);
      } catch (refreshError) {
        // Refresh failed - token is expired or invalid
        clearAuthData();
        window.location.href = '/login';
        return Promise.reject(refreshError);
      }
    }
    
    return Promise.reject(error);
  }
);

function isNetworkError(error: any) {
  return error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED' || error.message === 'Network Error';
}

async function refreshAccessToken(): Promise<string> {
  const refreshToken = localStorage.getItem('refreshToken');
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  
  const response = await api.post<{ access_token: string }>('/auth/refresh', { refresh_token: refreshToken });
  localStorage.setItem('accessToken', response.data.access_token);
  return response.data.access_token;
}

/**
 * Clears all authentication data from localStorage
 * This is called when JWT expires and refresh fails
 */
function clearAuthData(): void {
  // Remove auth tokens
  localStorage.removeItem('accessToken');
  localStorage.removeItem('refreshToken');
  
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
}

export default api;