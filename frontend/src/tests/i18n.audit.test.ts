/**
 * i18n audit — the code-side half of the translation contract.
 *
 * `scripts/translations/check_translations.py` (the family manager, wired in
 * CI) compares dictionaries with each other. It cannot see whether the UI
 * references a key that exists at all, so this test closes that gap:
 *
 *   1. every static `t('…')` key in `src/` resolves to a string in `en.json`
 *      (plural-aware: `key_one` + `key_other` also satisfy it);
 *   2. a key that resolves to an object (a mis-nested catalogue entry) fails —
 *      i18next renders the key, not the object, so it is a user-visible bug;
 *   3. `el.json` has exactly the `en.json` key set — no gaps, no orphans;
 *   4. interpolation placeholders match between the two locales, so a Greek
 *      string can never drop or invent a `{{variable}}`.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import en from '../locales/en.json';
import el from '../locales/el.json';

type Node = Record<string, unknown>;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const LOCALES = { el } as Record<string, Node>;

const SKIP_DIRS = new Set(['node_modules', 'dist', 'tests', '__tests__']);

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

const T_CALL = /\bt\(\s*['"]([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)['"]/g;
const T_TEMPLATE = /\bt\(\s*`([^`$]*)\$\{[^}]*\}([^`]*)`/g;

/**
 * Dynamic families rendered from backend or runtime values. Each shape is
 * `prefix<suffix`; a shape that matches no catalogue key would silently render
 * the raw key, so the test below fails on any such shape unless it is listed
 * here with the reason it is deliberately value-driven.
 */
const FALLBACK_BY_DESIGN: Record<string, string> = {
  'categories.':
    'document categories are tenant data (seeded concepts plus user-created names); the stored display name is the only correct label — translating it would fight user renames',
};

interface KeyRef {
  key: string;
  where: string;
}

function staticKeys(): KeyRef[] {
  const refs: KeyRef[] = [];
  for (const file of collectFiles(SRC)) {
    const source = readFileSync(file, 'utf8');
    for (const match of source.matchAll(T_CALL)) {
      const index = match.index ?? 0;
      const line = source.slice(0, index).split('\n').length;
      refs.push({ key: match[1], where: `${file.slice(SRC.length + 1)}:${line}` });
    }
  }
  return refs;
}

function resolve(root: Node, dotted: string): unknown {
  let current: unknown = root;
  for (const part of dotted.split('.')) {
    if (typeof current !== 'object' || current === null) return undefined;
    current = (current as Node)[part];
  }
  return current;
}

interface DynamicShape {
  shape: string;
  prefix: string;
  suffix: string;
  where: string;
}

function dynamicShapes(): DynamicShape[] {
  const seen = new Map<string, DynamicShape>();
  for (const file of collectFiles(SRC)) {
    const source = readFileSync(file, 'utf8');
    for (const match of source.matchAll(T_TEMPLATE)) {
      const prefix = match[1];
      const suffix = match[2];
      const shape = `${prefix}*${suffix}`;
      if (!seen.has(shape)) {
        const line = source.slice(0, match.index ?? 0).split('\n').length;
        seen.set(shape, { shape, prefix, suffix, where: `${file.slice(SRC.length + 1)}:${line}` });
      }
    }
  }
  return [...seen.values()];
}

function flatten(value: unknown, prefix = '', out: Map<string, string> = new Map()): Map<string, string> {
  if (typeof value === 'string') {
    out.set(prefix, value);
  } else if (typeof value === 'object' && value !== null) {
    for (const [key, child] of Object.entries(value as Node)) {
      flatten(child, prefix ? `${prefix}.${key}` : key, out);
    }
  } else {
    out.set(prefix, String(value));
  }
  return out;
}

function placeholders(value: string): string[] {
  return [...value.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map((m) => m[1]).sort();
}

const EN = flatten(en as Node);
const EN_KEYS = new Set(EN.keys());

describe('i18n audit — code keys resolve and locales stay in sync', () => {
  it('resolves every static t() key in en.json', () => {
    const missing: string[] = [];
    const notStrings: string[] = [];
    for (const { key, where } of staticKeys()) {
      const direct = resolve(en as Node, key);
      if (typeof direct === 'string') continue;
      const union = resolve(en as Node, `${key}_one`);
      const plural = resolve(en as Node, `${key}_other`);
      if (typeof union === 'string' && typeof plural === 'string') continue;
      if (direct === undefined) {
        missing.push(`${where} → ${key}`);
      } else {
        notStrings.push(`${where} → ${key} (${typeof direct})`);
      }
    }
    expect(missing.sort()).toEqual([]);
    expect(notStrings.sort()).toEqual([]);
  });

  it('has a catalogue entry for every dynamic key family', () => {
    const shapes = dynamicShapes();
    expect(shapes.length).toBeGreaterThan(20);

    const unsatisfied: string[] = [];
    for (const { shape, prefix, suffix, where } of shapes) {
      if (FALLBACK_BY_DESIGN[`${prefix}${suffix}`] !== undefined) continue;
      if (FALLBACK_BY_DESIGN[prefix] !== undefined) continue;
      const matches = [...EN_KEYS].filter(
        (key) =>
          key.startsWith(prefix) &&
          key.endsWith(suffix) &&
          key.length > prefix.length + suffix.length,
      );
      if (matches.length === 0) unsatisfied.push(`${where} → ${shape}`);
    }
    expect(unsatisfied.sort()).toEqual([]);
  });

  it('keeps no stale fallback-by-design entries', () => {
    const shapes = new Set(dynamicShapes().map(({ prefix, suffix }) => `${prefix}${suffix}`));
    const prefixes = new Set(dynamicShapes().map(({ prefix }) => prefix));
    const stale = Object.keys(FALLBACK_BY_DESIGN).filter(
      (entry) => !shapes.has(entry) && !prefixes.has(entry),
    );
    expect(stale.sort()).toEqual([]);
  });

  it.each(Object.keys(LOCALES))('%s declares exactly the source key set', (locale) => {
    const target = new Set(flatten(LOCALES[locale]).keys());
    const missing = [...EN_KEYS].filter((key) => !target.has(key));
    const extra = [...target].filter((key) => !EN_KEYS.has(key));
    expect(missing.sort()).toEqual([]);
    expect(extra.sort()).toEqual([]);
  });

  it.each(Object.keys(LOCALES))('%s keeps every interpolation placeholder', (locale) => {
    const target = flatten(LOCALES[locale]);
    const drift: string[] = [];
    for (const [key, source] of EN) {
      const translated = target.get(key);
      if (translated === undefined) continue;
      const expected = placeholders(source).join(',');
      const actual = placeholders(translated).join(',');
      if (expected !== actual) drift.push(`${key}: en[${expected}] ${locale}[${actual}]`);
    }
    expect(drift.sort()).toEqual([]);
  });

  it.each(Object.keys(LOCALES))('%s has no empty values', (locale) => {
    const empty = [...flatten(LOCALES[locale])]
      .filter(([, value]) => value.trim() === '')
      .map(([key]) => key);
    expect(empty.sort()).toEqual([]);
  });
});
