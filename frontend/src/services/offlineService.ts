import { db, PendingSync } from './db';
import axios from 'axios';

let isSyncingQueue = false;

// Create a background instance that DOES NOT have the offline-intercepting middleware
const backgroundApi = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add 401 retry to background instance to ensure sync works with new tokens
backgroundApi.interceptors.request.use(async (config) => {
  const token = localStorage.getItem('accessToken');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

backgroundApi.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;
      try {
        const refreshToken = localStorage.getItem('refreshToken');
        if (!refreshToken) throw new Error();
        
        const response = await axios.post(`${backgroundApi.defaults.baseURL}/auth/refresh`, { 
          refresh_token: refreshToken 
        });
        const newToken = response.data.access_token;
        localStorage.setItem('accessToken', newToken);
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return backgroundApi(originalRequest);
      } catch (err) {
        return Promise.reject(error);
      }
    }
    return Promise.reject(error);
  }
);

export const offlineService = {
  /**
   * Add a request to the pending sync queue
   */
  addToQueue: async (config: any) => {
    // Check if we already have this request to avoid duplicates
    // We only sync data-modifying requests
    if (['post', 'put', 'patch', 'delete'].includes(config.method?.toLowerCase() || '')) {
      // Don't queue auth requests
      if (config.url?.includes('auth/')) return;

      const pending: PendingSync = {
        method: config.method || 'POST',
        url: config.url || '',
        data: typeof config.data === 'string' ? JSON.parse(config.data) : config.data,
        headers: { 
          ...config.headers,
          // Remove transient headers
          'Authorization': undefined 
        },
        timestamp: Date.now(),
        retries: 0,
        status: 'pending'
      };
      
      await db.pendingSync.add(pending);
      console.log('Request queued for background sync:', pending.url);
    }
  },

  /**
   * Process all pending requests in the queue
   */
  processQueue: async () => {
    if (!navigator.onLine) return;

    // Use a lock to prevent concurrent sync processes
    if (isSyncingQueue) return;
    isSyncingQueue = true;

    try {
      const pending = await db.pendingSync
        .where('status')
        .equals('pending')
        .sortBy('timestamp');

      if (pending.length === 0) {
        isSyncingQueue = false;
        return;
      }

      console.log(`Syncing ${pending.length} pending requests...`);

      for (const item of pending) {
        try {
          await db.pendingSync.update(item.id!, { status: 'syncing' });

          await backgroundApi({
            method: item.method,
            url: item.url,
            data: item.data,
            headers: item.headers
          });
          
          await db.pendingSync.delete(item.id!);
          console.log(`Synced successfully: ${item.url}`);
        } catch (error) {
          console.error(`Sync failed for ${item.url}:`, error);
          
          const status = (axios.isAxiosError(error) && error.response && error.response.status < 500) 
            ? 'failed' 
            : 'pending';
            
          await db.pendingSync.update(item.id!, { 
            status, 
            retries: item.retries + 1 
          });
          
          // If it failed because of server error, stop replaying the queue to preserve order
          if (status === 'pending') break;
        }
      }
    } finally {
      isSyncingQueue = false;
    }
  },

  /**
   * Save a local draft
   */
  saveDraft: async (id: string, type: 'examination' | 'note' | 'biomarker', data: any) => {
    await db.localDrafts.put({
      id,
      type,
      data,
      updatedAt: Date.now()
    });
  },

  /**
   * Get a local draft
   */
  getDraft: async (id: string) => {
    return await db.localDrafts.get(id);
  },

  /**
   * Delete a draft after successful submission
   */
  deleteDraft: async (id: string) => {
    await db.localDrafts.delete(id);
  }
};
