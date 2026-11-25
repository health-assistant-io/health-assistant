/**
 * The one locale registry.
 *
 * Everything that needs to know which languages the app ships reads it from
 * here, and `src/tests/i18n.registry.test.ts` pins the four consumers together:
 *
 *   - `i18n.ts` (resources + `supportedLngs`)
 *   - `lib/uiLanguages.ts` (the language menus, which read display names from
 *     the `@neuronection/assistant-ui` reference catalogue — ADR-0024)
 *   - `scripts/translations/translations.toml` (`source.locale` +
 *     `dictionaries.locales`, the family gate's view)
 *   - `backend/app/core/settings_definitions.py` (`localization.language`
 *     options — the settings API's view)
 *
 * Adding a locale means adding it here first; the test fails until the
 * dictionary, the config and the backend enum agree.
 */
export const SHIPPED_LOCALES = ['en', 'el'] as const;

export type ShippedLocale = (typeof SHIPPED_LOCALES)[number];

/** The locale every other dictionary is measured against. */
export const SOURCE_LOCALE: ShippedLocale = 'en';
