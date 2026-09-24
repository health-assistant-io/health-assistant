import { NavigationRoute } from 'workbox-routing';
import { describe, expect, it } from 'vitest';

import { SERVER_NAVIGATION_DENYLIST } from '../swNavigation';

const route = new NavigationRoute(async () => new Response('shell'), {
  denylist: SERVER_NAVIGATION_DENYLIST,
});

const servesShell = (path: string): boolean => {
  const url = new URL(path, 'https://ha.example');
  const request = { mode: 'navigate' } as Request;
  return Boolean(
    route.match({ url, request, event: {} as ExtendableEvent, sameOrigin: true }),
  );
};

describe('service worker navigation fallback', () => {
  it.each([
    '/api/v1/integrations/fhir_server/oauth/callback?state=abc&code=xyz',
    '/api/v1/integrations/any_domain/oauth/callback',
    '/api/v1/users/me',
    '/health',
    '/health?probe=1',
    '/flower/',
    '/flower',
  ])('leaves %s to the network', (path) => {
    expect(servesShell(path)).toBe(false);
  });

  it.each([
    '/',
    '/patients',
    '/integrations',
    '/integrations/abc/details',
    '/healthcare',
    '/apis',
  ])('serves the SPA shell for %s', (path) => {
    expect(servesShell(path)).toBe(true);
  });
});
