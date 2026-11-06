/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';

const initScript = readFileSync(resolve(process.cwd(), 'public/theme-init.js'), 'utf8');

// The theme bootstrap is a blocking same-origin script in <head> (not an
// inline script, not a deferred module) so the stored dark-mode preference is
// on <html> before the first paint while script-src stays 'self'.
function runThemeInit(): void {
  new Function(initScript)();
}

describe('theme-init', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark');
  });

  it('applies the stored dark theme', () => {
    localStorage.setItem('settings-storage', JSON.stringify({ state: { theme: 'dark' } }));
    runThemeInit();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('keeps the default scheme when the stored theme is light', () => {
    localStorage.setItem('settings-storage', JSON.stringify({ state: { theme: 'light' } }));
    runThemeInit();
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('survives malformed storage', () => {
    localStorage.setItem('settings-storage', '{not json');
    expect(() => runThemeInit()).not.toThrow();
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });
});
