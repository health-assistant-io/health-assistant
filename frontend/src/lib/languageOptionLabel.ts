import { isLanguageCode, languageDisplayName } from '@neuronection/assistant-ui/languages';

import { UI_LANGUAGES } from './uiLanguages';

/**
 * Display label for a language option value (ADR-0024).
 *
 * Language names are catalog data, not translation keys: a shipped locale uses
 * its endonym (the same label the user menu shows, so the two surfaces cannot
 * drift), and any other ISO code resolves through Intl.DisplayNames in the
 * current UI language. Returns `undefined` when the value is not a language
 * code, so callers fall back to the raw option value.
 */
export function languageOptionLabel(code: string, uiLocale: string): string | undefined {
  if (!isLanguageCode(code)) return undefined;
  return (
    UI_LANGUAGES.find((language) => language.code === code)?.nativeName ??
    languageDisplayName(code, uiLocale)
  );
}
