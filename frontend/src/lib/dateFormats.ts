import { format as dateFnsFormat, formatDistanceToNow } from 'date-fns';
import i18n from 'i18next';

import { dateLocaleFor } from './dateLocale';

/**
 * One place for date formatting.
 *
 * Call sites pass a **style**, never a raw date-fns pattern: the pattern is
 * resolved per locale here, so Greek gets day-first order ("15 Ιαν") and its
 * own month and weekday names instead of English ones in English order.
 *
 * `src/tests/i18n.dateFormats.test.ts` fails on a locale-sensitive pattern
 * literal or a locale-less `toLocale*String()` anywhere else in `src/`.
 */

export type DateStyle =
  | 'short'
  | 'medium'
  | 'dateTime'
  | 'shortDateTime'
  | 'long'
  | 'full'
  | 'monthYear'
  | 'weekdayShort'
  | 'weekdayLong'
  | 'time';

/** Display patterns per locale; `en` is the fallback for a new locale. */
const DISPLAY_PATTERNS: Record<DateStyle, Record<'en' | 'el', string>> = {
  short: { en: 'MMM d', el: 'd MMM' },
  medium: { en: 'MMM d, yyyy', el: 'd MMM yyyy' },
  dateTime: { en: 'MMM d, yyyy HH:mm', el: 'd MMM yyyy HH:mm' },
  shortDateTime: { en: 'MMM d, HH:mm', el: 'd MMM HH:mm' },
  long: { en: 'MMMM d, yyyy', el: 'd MMMM yyyy' },
  full: { en: 'EEEE, MMMM d, yyyy', el: 'EEEE, d MMMM yyyy' },
  monthYear: { en: 'MMMM yyyy', el: 'MMMM yyyy' },
  weekdayShort: { en: 'eee', el: 'eee' },
  weekdayLong: { en: 'EEEE', el: 'EEEE' },
  time: { en: 'HH:mm', el: 'HH:mm' },
};

export const DATE_SHORT: DateStyle = 'short';
export const DATE_MEDIUM: DateStyle = 'medium';
export const DATE_TIME_MEDIUM: DateStyle = 'dateTime';
export const DATE_SHORT_TIME: DateStyle = 'shortDateTime';
export const DATE_LONG: DateStyle = 'long';
export const DATE_FULL: DateStyle = 'full';
export const DATE_MONTH_YEAR: DateStyle = 'monthYear';
export const WEEKDAY_SHORT: DateStyle = 'weekdayShort';
export const WEEKDAY_LONG: DateStyle = 'weekdayLong';
export const TIME_SHORT: DateStyle = 'time';

// Machine patterns (stable, never localized).
export const MACHINE_DATE = 'yyyy-MM-dd';
export const MACHINE_STAMP = 'yyyyMMdd';

export function uiLocale(): string {
  // The `i18next` default export is the instance `src/i18n.ts` initializes.
  // Reading it directly (instead of importing that module) keeps formatting
  // free of a react-i18next dependency, so a test that mocks the hook still
  // gets correct date output.
  return i18n.resolvedLanguage ?? i18n.language ?? 'en';
}

function patternFor(style: DateStyle): string {
  const base = uiLocale().split('-')[0] as 'en' | 'el';
  return DISPLAY_PATTERNS[style][base] ?? DISPLAY_PATTERNS[style].en;
}

export function formatDate(date: Date | number, style: DateStyle): string {
  return dateFnsFormat(date, patternFor(style), { locale: dateLocaleFor(uiLocale()) });
}

export function formatRelative(date: Date | number): string {
  return formatDistanceToNow(date, { addSuffix: true, locale: dateLocaleFor(uiLocale()) });
}

export function formatTimestamp(value: Date | number): string {
  return new Date(value).toLocaleString(uiLocale());
}

export function formatDay(value: Date | number): string {
  return new Date(value).toLocaleDateString(uiLocale());
}

export function formatNumber(value: number): string {
  return value.toLocaleString(uiLocale());
}
