import { enUS, el } from 'date-fns/locale';
import type { Locale } from 'date-fns';

const DATE_LOCALES: Record<string, Locale> = {
  en: enUS,
  el,
};

/**
 * date-fns locale for a resolved i18n language.
 *
 * `i18n` runs with `load: 'languageOnly'`, so `el-GR` is already `el`; the
 * split is a guard for callers that pass a raw detector/browser tag.
 */
export function dateLocaleFor(language: string | undefined): Locale {
  const base = (language ?? 'en').split('-')[0];
  return DATE_LOCALES[base] ?? enUS;
}
