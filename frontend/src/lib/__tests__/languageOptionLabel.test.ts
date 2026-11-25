import { describe, expect, it } from 'vitest';

import { languageOptionLabel } from '../languageOptionLabel';

describe('languageOptionLabel', () => {
  it('uses the shipped endonym so the settings picker matches the user menu', () => {
    expect(languageOptionLabel('el', 'en')).toBe('Ελληνικά');
    expect(languageOptionLabel('en', 'el')).toBe('English');
  });

  it('resolves a non-shipped code through the catalog in the current UI language', () => {
    expect(languageOptionLabel('de', 'en')).toBe('German');
  });

  it('returns undefined for a value that is not a language code', () => {
    expect(languageOptionLabel('metric', 'en')).toBeUndefined();
    expect(languageOptionLabel('dark', 'en')).toBeUndefined();
  });
});
