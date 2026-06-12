/**
 * Validates JWT token by checking with the backend
 * Returns true if valid, false if expired/invalid
 */
export async function validateToken(token: string): Promise<boolean> {
  try {
    const response = await fetch(`${import.meta.env.VITE_API_URL || '/api/v1'}/auth/validate`, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    if (!response.ok) {
      return false;
    }
    const data = await response.json();
    return data.valid === true;
  } catch (error) {
    return false;
  }
}

/**
 * Clears all authentication and session data from localStorage, cookies, IndexedDB, and Cache API
 */
export async function clearAuthData(): Promise<void> {
  // 1. Clear LocalStorage related to session
  const keysToRemove = [
    'accessToken',
    'refreshToken',
    'authStore', // Usually contains user/session info
    'user',
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
  
  // Also clear any other prefixed keys if used
  Object.keys(localStorage).forEach(key => {
    if (key.includes('patient') || key.includes('examination') || key.includes('auth')) {
      localStorage.removeItem(key);
    }
  });

  // 2. Clear SessionStorage
  sessionStorage.clear();

  // 3. Clear all Cookies
  const cookies = document.cookie.split(";");
  for (let i = 0; i < cookies.length; i++) {
    const cookie = cookies[i];
    const eqPos = cookie.indexOf("=");
    const name = eqPos > -1 ? cookie.substr(0, eqPos).trim() : cookie.trim();
    // Try to remove from common paths and domains
    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/`;
    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=${window.location.hostname}`;
  }

  // 4. Clear Cache API (Service Worker caches)
  if ('caches' in window) {
    try {
      const names = await caches.keys();
      await Promise.all(names.map(name => caches.delete(name)));
    } catch (e) {
      console.error("Failed to clear cache:", e);
    }
  }

  // 5. Clear IndexedDB
  if (window.indexedDB) {
    // Explicitly delete our known DBs
    const dbNames = ['health_assistant-offline', 'health_assistant-cache', 'workbox-precache-v2'];
    dbNames.forEach(dbName => {
      try {
        window.indexedDB.deleteDatabase(dbName);
      } catch (e) {
        console.error(`Failed to delete IndexedDB ${dbName}:`, e);
      }
    });

    // Attempt to delete all databases if supported
    if (window.indexedDB.databases) {
      try {
        const dbs = await window.indexedDB.databases();
        await Promise.all(dbs.map(db => {
          if (db.name) return window.indexedDB.deleteDatabase(db.name);
          return Promise.resolve();
        }));
      } catch (e) {
        console.error("Failed to clear all IndexedDBs:", e);
      }
    }
  }

  // 6. Unregister all Service Workers
  if ('serviceWorker' in navigator) {
    try {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map(r => r.unregister()));
    } catch (e) {
      console.error("Failed to unregister Service Worker:", e);
    }
  }
}