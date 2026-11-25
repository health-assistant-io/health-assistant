import { describe, expect, it } from 'vitest';
import { filterLabels, fromLibraryValue, toLibraryFacet, toLibraryValue } from '../adapter';
import type { FacetDefinition } from '../types';

const t = (key: string, options?: Record<string, unknown>) =>
  (options?.defaultValue as string | undefined) ?? key;

describe('filter value mapping', () => {
  it('maps a nullable single value onto the library empty string and back', () => {
    expect(toLibraryValue({ kind: 'single', value: null })).toEqual({ kind: 'single', value: '' });
    expect(fromLibraryValue({ kind: 'single', value: '' })).toEqual({ kind: 'single', value: null });

    expect(toLibraryValue({ kind: 'single', value: 'lipids' })).toEqual({ kind: 'single', value: 'lipids' });
    expect(fromLibraryValue({ kind: 'single', value: 'lipids' })).toEqual({ kind: 'single', value: 'lipids' });
  });

  it('round-trips multi and toggle values unchanged', () => {
    expect(toLibraryValue({ kind: 'multi', values: ['a', 'b'] })).toEqual({ kind: 'multi', values: ['a', 'b'] });
    expect(fromLibraryValue({ kind: 'multi', values: ['a', 'b'] })).toEqual({ kind: 'multi', values: ['a', 'b'] });

    expect(toLibraryValue({ kind: 'toggle', on: true })).toEqual({ kind: 'toggle', on: true });
    expect(fromLibraryValue({ kind: 'toggle', on: true })).toEqual({ kind: 'toggle', on: true });
  });

  it('has no library representation for a range value or facet', () => {
    expect(toLibraryValue({ kind: 'range', min: 1, max: 2 })).toBeNull();

    const rangeFacet: FacetDefinition<never> = { id: 'r', label: 'Range', kind: 'range', mode: 'client' };
    expect(toLibraryFacet(rangeFacet, t)).toBeNull();
  });

  it('resolves a facet label through its i18n key, falling back to the literal', () => {
    const keyed: FacetDefinition<never> = {
      id: 'k',
      label: 'Category',
      i18nKey: 'filters.category',
      kind: 'multi',
      mode: 'client',
    };
    expect(toLibraryFacet(keyed, (key: string) => `translated:${key}`)?.label).toBe('translated:filters.category');

    const literal: FacetDefinition<never> = { id: 'l', label: 'Category', kind: 'multi', mode: 'client' };
    expect(toLibraryFacet(literal, t)?.label).toBe('Category');
  });

  it('carries every label the bar and chip need, with no English default', () => {
    const labels = filterLabels((key: string) => `key:${key}`);
    expect(labels.more).toBe('key:filters.more');
    expect(labels.clearAll).toBe('key:filters.clear_all');
    expect(labels.searchPlaceholder).toBe('key:filters.search_options_placeholder');
    expect((labels.removeNamed as (label: string) => string)('Lipids')).toBe('key:common.remove_named');
    expect((labels.optionWithCount as (label: string, count: number) => string)('Lipids', 3)).toBe(
      'key:filters.option_with_count',
    );
  });
});
