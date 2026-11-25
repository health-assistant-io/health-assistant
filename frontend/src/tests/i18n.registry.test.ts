/**
 * Locale registry consistency.
 *
 * The shipped language list lived in four places and drifted: the frontend
 * `i18n` resources, the language menus, the family translation config, and the
 * backend `localization.language` setting. That drift is what let the settings
 * dropdown offer a language the catalogue had no keys for.
 *
 * Every consumer now derives from `src/lib/locales.ts`; this test proves that
 * the derived views (translation config + backend enum) still agree with it —
 * including the case where someone registers a locale on only one side.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import i18n from '../i18n';
import { SHIPPED_LOCALES, SOURCE_LOCALE } from '../lib/locales';
import { UI_LANGUAGES } from '../lib/uiLanguages';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const TOML = join(ROOT, 'scripts', 'translations', 'translations.toml');
const SETTINGS = join(ROOT, 'backend', 'app', 'core', 'settings_definitions.py');

function tomlLocales(): { source: string; targets: string[] } {
  const text = readFileSync(TOML, 'utf8');
  const source = /\[source\][\s\S]*?locale\s*=\s*"([a-z-]+)"/i.exec(text)?.[1];
  const list = /\[dictionaries\][\s\S]*?locales\s*=\s*\[([^\]]*)\]/.exec(text)?.[1] ?? '';
  return {
    source: source ?? '',
    targets: [...list.matchAll(/"([a-z-]+)"/gi)].map((m) => m[1]),
  };
}

function backendLanguageOptions(): string[] {
  const text = readFileSync(SETTINGS, 'utf8');
  const block = /key="localization\.language"[\s\S]*?options=\[([\s\S]*?)\]/.exec(text)?.[1] ?? '';
  return [...block.matchAll(/SettingEnumOption\(value="([a-z-]+)"\)/gi)].map((m) => m[1]);
}

describe('i18n — one locale registry', () => {
  it('registers the same languages in i18n, the menus, the gate config and the backend', () => {
    expect([...SHIPPED_LOCALES]).toEqual(['en', 'el']);
    expect(UI_LANGUAGES.map((language) => language.code)).toEqual([...SHIPPED_LOCALES]);

    const { source, targets } = tomlLocales();
    expect([source, ...targets].sort()).toEqual([...SHIPPED_LOCALES].sort());
    expect(source).toBe(SOURCE_LOCALE);

    expect(backendLanguageOptions().sort()).toEqual([...SHIPPED_LOCALES].sort());
  });

  it('loads a dictionary for every shipped language', () => {
    const resources = Object.keys(i18n.options.resources ?? {});
    expect(resources.sort()).toEqual([...SHIPPED_LOCALES].sort());
    for (const language of SHIPPED_LOCALES) {
      const bundle = i18n.getResourceBundle(language, 'translation');
      expect(Object.keys(bundle ?? {}).length).toBeGreaterThan(0);
    }
  });

  it('supports exactly the shipped languages (a browser tag resolves to one of them)', async () => {
    await i18n.changeLanguage('de-DE');
    expect(SHIPPED_LOCALES).toContain(i18n.resolvedLanguage);
    await i18n.changeLanguage(SOURCE_LOCALE);
  });
});
