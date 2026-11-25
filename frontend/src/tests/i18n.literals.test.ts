/**
 * Hardcoded-copy budget.
 *
 * User-visible English that never calls `t()` is invisible to every other
 * gate: the dictionary gate compares dictionaries, the audit only sees `t()`
 * calls, and the backend gate only sees keys the backend emits. A literal JSX
 * text node or a `title`/`placeholder`/`aria-label`/`alt` string is copy that
 * cannot be translated and silently ships English into the Greek UI.
 *
 * This is a **ratchet**: `i18n.literals.baseline.json` records the current
 * count per file, and any increase fails — new copy must go through `t()`.
 * Converting a literal lowers the count, and `I18N_LITERALS_UPDATE=1` rewrites
 * the baseline (run it only after a conversion, never to accept a regression).
 *
 *   I18N_LITERALS_UPDATE=1 pnpm exec vitest run src/tests/i18n.literals.test.ts
 *
 * A lint rule (`eslint-plugin-i18next`) would be the end state; it cannot gate
 * today because it flags every existing literal at error level, and a
 * warn-level rule does not fail CI. Once this budget reaches zero, switch.
 */
import { readFileSync, readdirSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const BASELINE = join(SRC, 'tests', 'i18n.literals.baseline.json');
const SKIP_DIRS = new Set(['node_modules', 'dist', 'tests', '__tests__']);

const TEXT_NODE = />\s*([A-Z][A-Za-z][A-Za-z0-9 ,.'’%()/–—&-]{6,60})\s*</g;
const ATTRIBUTE = /(?:placeholder|title|aria-label|alt)="([A-Z][^"{}]{3,60})"/g;
const WORD = /\b[a-z]{3,}\b/;

interface Counts {
  text: number;
  attrs: number;
}

type Baseline = Record<string, Counts>;

function collectFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(name)) out.push(...collectFiles(full));
    } else if (name.endsWith('.tsx') && !/\.test\.tsx$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

function scan(): Baseline {
  const counts: Baseline = {};
  for (const file of collectFiles(SRC)) {
    const rel = file.slice(SRC.length + 1);
    // <code> content is machine text (endpoints, patterns, ids), not copy.
    const source = readFileSync(file, 'utf8').replace(/<code[^>]*>[\s\S]*?<\/code>/g, '');
    let text = 0;
    for (const match of source.matchAll(TEXT_NODE)) {
      if (WORD.test(match[1])) text += 1;
    }
    ATTRIBUTE.lastIndex = 0;
    const attrs = [...source.matchAll(ATTRIBUTE)].filter((m) => WORD.test(m[1])).length;
    if (text || attrs) counts[rel] = { text, attrs };
  }
  return counts;
}

const current = scan();

describe('i18n — hardcoded copy budget', () => {
  it('adds no new untranslated text or attribute copy', () => {
    if (process.env.I18N_LITERALS_UPDATE === '1') {
      writeFileSync(BASELINE, `${JSON.stringify(current, null, 2)}\n`);
      return;
    }
    expect(existsSync(BASELINE), 'missing baseline — run with I18N_LITERALS_UPDATE=1').toBe(true);
    const baseline: Baseline = JSON.parse(readFileSync(BASELINE, 'utf8'));
    const regressions: string[] = [];

    for (const [file, counts] of Object.entries(current)) {
      const allowed = baseline[file] ?? { text: 0, attrs: 0 };
      if (counts.text > allowed.text) {
        regressions.push(`${file}: ${counts.text - allowed.text} new text node(s)`);
      }
      if (counts.attrs > allowed.attrs) {
        regressions.push(`${file}: ${counts.attrs - allowed.attrs} new attribute string(s)`);
      }
    }
    expect(regressions.sort()).toEqual([]);
  });
});
