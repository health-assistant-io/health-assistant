import { afterEach, describe, expect, it } from 'vitest';

import i18n from '../../i18n';
import {
  DATE_LONG,
  DATE_SHORT,
  WEEKDAY_LONG,
  WEEKDAY_SHORT,
  formatDate,
  uiLocale,
} from '../dateFormats';

const DATE = new Date(2026, 0, 15, 14, 30);

afterEach(async () => {
  await i18n.changeLanguage('en');
});

describe('dateFormats', () => {
  it('renders Greek month and weekday names in Greek', async () => {
    await i18n.changeLanguage('el');
    expect(uiLocale()).toBe('el');
    expect(formatDate(DATE, DATE_SHORT)).toBe('15 Ιαν');
    expect(formatDate(DATE, DATE_LONG)).toBe('15 Ιανουαρίου 2026');
    expect(formatDate(DATE, WEEKDAY_SHORT)).toBe('Πέμ');
    expect(formatDate(DATE, WEEKDAY_LONG)).toBe('Πέμπτη');
  });

  it('renders English names in English', async () => {
    await i18n.changeLanguage('en');
    expect(formatDate(DATE, DATE_SHORT)).toBe('Jan 15');
    expect(formatDate(DATE, DATE_LONG)).toBe('January 15, 2026');
    expect(formatDate(DATE, WEEKDAY_SHORT)).toBe('Thu');
  });

  it('resolves a region-tagged language to its base locale', async () => {
    await i18n.changeLanguage('el-GR');
    expect(uiLocale()).toBe('el');
    expect(formatDate(DATE, DATE_SHORT)).toBe('15 Ιαν');
  });
});
