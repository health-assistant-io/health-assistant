import { describe, expect, it } from 'vitest';
import serveConfig from '../../public/serve.json';

// The production SPA policy ships as public/serve.json and is applied by the
// `serve` runtime in the frontend image. The API serves the identical body
// (backend/app/core/csp.py); backend/tests/test_csp_headers.py pins the pair.
function productionCsp(): string {
  const header = serveConfig.headers
    .flatMap((rule) => rule.headers)
    .find((h) => h.key.toLowerCase() === 'content-security-policy');
  if (!header) throw new Error('serve.json carries no Content-Security-Policy');
  return header.value;
}

function directives(policy: string): Record<string, string[]> {
  return Object.fromEntries(
    policy
      .split(';')
      .map((part) => part.trim().split(/\s+/))
      .filter((tokens) => tokens[0])
      .map(([name, ...values]) => [name, values]),
  );
}

describe('production CSP', () => {
  it('locks scripts to same-origin, with no inline or eval', () => {
    const parsed = directives(productionCsp());
    expect(parsed['script-src']).toEqual(["'self'"]);
  });

  it('keeps every source self-hosted', () => {
    const policy = productionCsp();
    expect(policy).not.toMatch(/https?:\/\//);
    expect(policy).not.toMatch(/\*/);
  });

  it('allows the same-origin document preview and blob assets', () => {
    const parsed = directives(productionCsp());
    expect(parsed['frame-ancestors']).toEqual(["'self'"]);
    expect(parsed['img-src']).toContain('blob:');
    expect(parsed['media-src']).toContain('blob:');
  });

  it('keeps connect-src truthful (same origin only)', () => {
    expect(directives(productionCsp())['connect-src']).toEqual(["'self'"]);
  });
});
