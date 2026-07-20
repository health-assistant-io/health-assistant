/**
 * @file Multilingual search index for clinical-event types.
 *
 * Problem: the form's type-picker search needs to match BOTH the English
 * source strings (what the backend stores) AND the user's current-locale
 * translations (what the UI displays). Otherwise a Greek user who sees
 * "Εγκυμοσύνη" on screen can't find it by typing "εγκυμο" — the search
 * would only see the English backend string "Pregnancy".
 *
 * Solution: precompute a per-type "haystack" blob that concatenates all
 * searchable fields in both languages, then search against that. The index
 * rebuilds only when the underlying types list changes OR the active
 * language changes — never per keystroke.
 *
 * Why this shape (vs. calling `t()` inline in the filter):
 *  - Performance: 9 types × 4 t() calls = 36 lookups per keystroke is
 *    wasteful when the user is typing. The index does it once.
 *  - Testability: the builder + matcher are pure functions — no React,
 *    no hook plumbing. vitest can hit them directly.
 *  - Separation: the form component stays focused on layout/state; the
 *    search contract lives here.
 */
import type { TFunction } from 'i18next';
import type { ClinicalEventType } from '../services/clinicalEventService';

export interface SearchableType {
  type: ClinicalEventType;
  /** Lowercased concatenation of all searchable fields, English + locale. */
  haystack: string;
}

/**
 * Build a search index over a list of clinical-event types. Each entry
 * includes the type's English backend fields (always searched) AND the
 * localized fields from `events.type.*` / `events.category.*` i18n keys.
 *
 * If the current locale IS English, the localized lookups return the same
 * English strings (the i18n fallback) — the haystack ends up with some
 * duplicate text but the search behavior is unchanged.
 *
 * The returned array is stable for a given (types, t.language) pair, so
 * memoizing the result of this function in a `useMemo([types, i18n.language])`
 * is correct — the index rebuilds only when types or language actually
 * change, not per render.
 */
export function buildSearchableIndex(
  types: ClinicalEventType[],
  t: TFunction,
): SearchableType[] {
  return types.map(type => {
    const cat = type.category_concept;

    // English source — always searchable. These are the raw backend strings
    // (slug, name, description for both type + its category).
    const englishFields: string[] = [
      type.name,
      type.description || '',
      type.slug,
      cat?.name || '',
      cat?.description || '',
      cat?.slug || '',
    ];

    // Current-locale translations. The fallback (2nd arg) is the backend
    // string — when the i18n key is missing (e.g. a custom tenant-created
    // type), the localized haystack degrades to the English source. No data
    // is lost; the type is still searchable by its backend name.
    const localizedFields: string[] = [
      t(`events.type.${type.slug}.name`, type.name),
      t(`events.type.${type.slug}.description`, type.description || ''),
      cat ? t(`events.category.${cat.slug}.name`, cat.name) : '',
      cat ? t(`events.category.${cat.slug}.description`, cat.description || '') : '',
    ];

    return {
      type,
      haystack: [...englishFields, ...localizedFields].join(' ').toLowerCase(),
    };
  });
}

/**
 * Match a query string against a prebuilt index. Returns the underlying
 * `ClinicalEventType` objects (not the index entries) so callers can use
 * them directly.
 *
 * Empty / whitespace-only query returns `[]` (matches the existing form
 * behavior — `isSearching` gates this anyway, but the helper is defensive).
 *
 * Matching is a case-insensitive substring test. For Greek diacritic-
 * insensitive search (e.g. "εγκυμοσυνη" matching "εγκυμοσύνη"), see the
 * out-of-scope note in the dev plan — would require a `.normalize('NFD')`
 * + diacritic-strip pass on both haystack and query.
 */
export function searchTypes(
  index: SearchableType[],
  query: string,
): ClinicalEventType[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return index
    .filter(({ haystack }) => haystack.includes(q))
    .map(({ type }) => type);
}
