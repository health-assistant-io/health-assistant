/**
 * Navigations the service worker must leave to the network.
 *
 * Mirrors the server-owned locations in `docker/nginx.conf`. Workbox matches
 * these against `pathname + search`.
 */
export const SERVER_NAVIGATION_DENYLIST: RegExp[] = [
  /^\/api\//,
  /^\/health(?:\?|$)/,
  /^\/flower(?:\/|\?|$)/,
];
