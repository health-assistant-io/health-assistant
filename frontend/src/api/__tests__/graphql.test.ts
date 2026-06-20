/**
 * Tests for audit items A11, A12, A13 (frontend PWA + GraphQL hygiene).
 *
 * A11: PWA manifest shortcut to /examinations/new — route does not exist
 *      (actual is /examinations/upload). Falls back to Dashboard.
 * A12: PWA runtime caches were hardcoded to http://localhost:8000 —
 *      never matched in any non-local deployment.
 * A13: GraphQL client set Authorization header once at module-load;
 *      never refreshed. Every call 401'd after the first token rotation.
 *
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const VITE_CONFIG_PATH = resolve(__dirname, '../../../vite.config.ts');

// ---------------------------------------------------------------------------
// A11: manifest shortcut must point at a real route
// ---------------------------------------------------------------------------

describe('A11 — PWA manifest shortcut', () => {
  it('points /examinations/new at the real /examinations/upload route', () => {
    const config = readFileSync(VITE_CONFIG_PATH, 'utf8');
    // The buggy form
    expect(config).not.toMatch(/url:\s*['"]\/examinations\/new['"]/);
    // The correct form
    expect(config).toMatch(/url:\s*['"]\/examinations\/upload['"]/);
  });
});

// ---------------------------------------------------------------------------
// A12: PWA runtime caches must NOT be hardcoded to localhost:8000
// ---------------------------------------------------------------------------

describe('A12 — PWA runtime caching', () => {
  it('does not contain the hardcoded localhost:8000 url pattern in runtime caches', () => {
    const config = readFileSync(VITE_CONFIG_PATH, 'utf8');
    // The buggy form was urlPattern: /^http:\/\/localhost:8000\/.../
    // The proxy block may legitimately use localhost:8000 as a fallback
    // target — that's fine. What's broken is the runtime cache matcher.
    expect(config).not.toMatch(/urlPattern:\s*\/\^http:\\\/\\\/localhost:8000/);
  });

  it('uses same-origin predicate functions for runtime caches', () => {
    const config = readFileSync(VITE_CONFIG_PATH, 'utf8');
    // Each runtime cache entry should now use the same-origin callback
    // form, not a hardcoded URL regex.
    expect(config).toMatch(/sameOrigin/);
    expect(config).toMatch(/url\.pathname\.startsWith\(['"]\/api\/v1\/auth\/me['"]\)/);
    expect(config).toMatch(
      /url\.pathname\.startsWith\(['"]\/api\/v1\/biomarkers['"]\)/,
    );
  });
});

// ---------------------------------------------------------------------------
// A13: GraphQL client must NOT set Authorization once at module load
// ---------------------------------------------------------------------------

describe('A13 — GraphQL client auth header', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('does not set Authorization at module load (no stale header)', async () => {
    // No token in localStorage at import time
    const mod = await import('../graphql');
    // graphqlClient itself must not carry an Authorization header that
    // was captured at module load.
    const header = mod.graphqlClient.requestConfig.headers;
    if (header && typeof header === 'object') {
      const auth = (header as Record<string, string>).Authorization;
      expect(auth).toBeFalsy();
    }
  });

  it('graphqlRequest attaches the live token on each call', async () => {
    const mod = await import('../graphql');
    // Spy on the underlying client's setHeader and request method.
    const setHeaderSpy = vi.spyOn(mod.graphqlClient, 'setHeader');
    const requestSpy = vi
      .spyOn(mod.graphqlClient, 'request')
      .mockResolvedValue({} as never);

    // First call with token A
    localStorage.setItem('accessToken', 'token-a');
    await mod.graphqlRequest('query { hello }');
    expect(setHeaderSpy).toHaveBeenCalledWith('Authorization', 'Bearer token-a');
    expect(requestSpy).toHaveBeenCalledTimes(1);

    // Rotate the token; second call must use the new token, not the old one.
    setHeaderSpy.mockClear();
    requestSpy.mockClear();
    localStorage.setItem('accessToken', 'token-b-rotated');
    await mod.graphqlRequest('query { hello }');
    expect(setHeaderSpy).toHaveBeenCalledWith(
      'Authorization',
      'Bearer token-b-rotated',
    );
    expect(requestSpy).toHaveBeenCalledTimes(1);

    requestSpy.mockRestore();
  });

  it('graphqlRequest clears the header when no token is present', async () => {
    const mod = await import('../graphql');
    const setHeaderSpy = vi.spyOn(mod.graphqlClient, 'setHeader');
    const requestSpy = vi
      .spyOn(mod.graphqlClient, 'request')
      .mockResolvedValue({} as never);

    // No token in storage
    await mod.graphqlRequest('query { hello }');
    expect(setHeaderSpy).toHaveBeenCalledWith('Authorization', '');

    requestSpy.mockRestore();
  });
});
