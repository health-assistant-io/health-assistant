/// <reference lib="webworker" />
import { createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';

declare const self: ServiceWorkerGlobalScope;

precacheAndRoute(self.__WB_MANIFEST);

// Offline navigation fallback: serve the precached SPA shell for in-app
// navigations (dev is excluded — the dev server owns routing/HMR). API and
// websocket paths are denylisted: they must always reach the network. The
// documents explorer embeds /api/v1/documents/{id}/download in an <iframe>
// (AuthenticatedPdf / PdfViewer) — an iframe load IS a navigation request,
// and answering it with the cached index.html (whose cached headers carry
// the gateway's CSP frame-ancestors 'none') broke the preview with a
// cspBlocked neterror + an SW "unexpected error" (first seen on the
// 2026-10 live demo).
if (import.meta.env.PROD) {
  registerRoute(
    new NavigationRoute(createHandlerBoundToURL('/index.html'), {
      denylist: [/^\/api\//, /^\/ws/],
    })
  );
}

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

    // Delivery status is tracked server-side by the push delivery worker
    // (NotificationDelivery row updated when the push is accepted), so no
    // client callback is needed here.
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
