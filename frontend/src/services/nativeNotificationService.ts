import { notificationService } from './notificationService';

/**
 * Helper to convert VAPID key
 */
function urlBase64ToUint8Array(base64String: string) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding)
    .replace(/\-/g, '+')
    .replace(/_/g, '/');

  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);

  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export const nativeNotificationService = {
  /**
   * Request permission and subscribe to Web Push
   */
  subscribeToPush: async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      console.warn('Web Push is not supported in this browser');
      return null;
    }

    try {
      console.log('Requesting service worker registration...');
      let registration = await navigator.serviceWorker.getRegistration();
      
      if (!registration) {
        console.log('No registration found, waiting for ready...');
        registration = await navigator.serviceWorker.ready;
      }

      const publicKey = await notificationService.getVapidPublicKey();
      
      if (!publicKey) {
        throw new Error('VAPID Public Key not found on server. Please check backend .env file.');
      }

      console.log('Subscribing to Push Manager...');
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey)
      });

      // Register with backend
      await notificationService.subscribe(subscription.toJSON());
      console.log(
        'Push subscription registered with backend. Endpoint:',
        subscription.endpoint
      );
      return subscription;
    } catch (error) {
      console.error('Failed to subscribe to push notifications:', error);
      return null;
    }
  },

  /**
   * Request permission for native notifications
   */
  requestPermission: async (): Promise<NotificationPermission> => {
    if (!('Notification' in window)) {
      console.warn('This browser does not support desktop notification');
      return 'denied';
    }

    if (Notification.permission === 'granted') {
      return 'granted';
    }

    return await Notification.requestPermission();
  },

  /**
   * Show a native notification
   */
  showNotification: async (title: string, options?: NotificationOptions) => {
    if (!('Notification' in window)) return;

    if (Notification.permission === 'granted') {
      return new Notification(title, {
        icon: '/icon.svg',
        badge: '/icon.svg',
        ...options
      });
    } else if (Notification.permission !== 'denied') {
      const permission = await Notification.requestPermission();
      if (permission === 'granted') {
        return new Notification(title, {
          icon: '/icon.svg',
          badge: '/icon.svg',
          ...options
        });
      }
    }
  },

  /**
   * Check if permission is already granted
   */
  isPermissionGranted: (): boolean => {
    return 'Notification' in window && Notification.permission === 'granted';
  }
};
