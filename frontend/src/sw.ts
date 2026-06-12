/// <reference lib="webworker" />
import { precacheAndRoute } from 'workbox-precaching';

declare const self: ServiceWorkerGlobalScope;

precacheAndRoute(self.__WB_MANIFEST);

self.addEventListener('push', (event) => {
  if (event.data) {
    const data = event.data.json();
    const title = data.title || 'Health Assistant Notification';
    const notificationId = data.id;

    const options = {
      body: data.body,
      icon: '/icon.svg',
      badge: '/icon.svg',
      data: {
        ...data.payload,
        notificationId: notificationId
      },
      vibrate: [100, 50, 100],
      actions: [
        { action: 'open', title: 'Open App' },
        { action: 'close', title: 'Dismiss' }
      ]
    };

    // Mark as delivered in backend
    if (notificationId) {
      event.waitUntil(
        fetch(`/api/v1/notifications/${notificationId}/delivered`, {
          method: 'PATCH'
        }).catch(err => console.error('Failed to mark notification as delivered:', err))
      );
    }

    event.waitUntil(self.registration.showNotification(title, options));
  }
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  if (event.action === 'close') return;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        // Match any page of our app
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow('/');
      }
    })
  );
});
