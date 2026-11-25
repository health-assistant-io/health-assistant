/**
 * Date/number formatting guard.
 *
 * Every user-facing date goes through `src/lib/dateFormats.ts`, which applies
 * the resolved UI locale. Before this guard, 33 call sites formatted with a
 * bare `format(date, 'MMM d')` and 42 rendered `toLocaleDateString()` with no
 * locale — both follow the browser/OS locale instead of the app language, so a
 * Greek UI showed English month and weekday names.
 *
 * A new locale-sensitive literal or a locale-less `toLocale*String()` fails
 * here rather than in a Greek user's screenshot.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const HELPERS = 'lib/dateFormats.ts';
const SKIP_DIRS = new Set(['node_modules', 'dist', 'tests', '__tests__']);

const SENSITIVE_TOKENS = ['MMM', 'MMMM', 'eee', 'EEEE'];
const TOKEN_LITERAL = new RegExp(`'[^']*\\b(${SENSITIVE_TOKENS.join('|')})\\b[^']*'`);
const LOCALE_LESS = /\.toLocale(?:Date|Time)?String\(\s*\)/;
const INTL_WITHOUT_LOCALE = /\bIntl\.(?:NumberFormat|DateTimeFormat|RelativeTimeFormat)\(\s*(?!['"])/;

function collectFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(name)) out.push(...collectFiles(full));
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !/\.d\.ts$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

function violationsOf(pattern: RegExp): string[] {
  const found: string[] = [];
  for (const file of collectFiles(SRC)) {
    const rel = file.slice(SRC.length + 1);
    if (rel === HELPERS) continue;
    const lines = readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, index) => {
      pattern.lastIndex = 0;
      if (pattern.test(line)) found.push(`${rel}:${index + 1} — ${line.trim().slice(0, 120)}`);
    });
  }
  return found;
}

describe('date and number formatting stay locale-aware', () => {
  it('keeps locale-sensitive format tokens inside the helper module', () => {
    expect(violationsOf(TOKEN_LITERAL).sort()).toEqual([]);
  });

  it('never calls toLocale*String() without a locale', () => {
    expect(violationsOf(LOCALE_LESS).sort()).toEqual([]);
  });

  it('never builds an Intl formatter without a locale', () => {
    expect(violationsOf(INTL_WITHOUT_LOCALE).sort()).toEqual([]);
  });
});
