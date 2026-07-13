import { describe, it, expect } from 'vitest';
import { deriveCountedOptions } from '../deriveCountedOptions';

interface Item {
  tag?: string | null;
}

const items: Item[] = [
  { tag: 'Lipids' },
  { tag: 'Lipids' },
  { tag: 'Glucose' },
  { tag: null },
  { tag: undefined },
  {},
  { tag: '' },
  { tag: 'Hormones' },
];

describe('deriveCountedOptions', () => {
  it('counts occurrences per distinct value', () => {
    const opts = deriveCountedOptions(items, (i) => i.tag);
    const lipids = opts.find((o) => o.value === 'Lipids');
    expect(lipids?.count).toBe(2);
    const glucose = opts.find((o) => o.value === 'Glucose');
    expect(glucose?.count).toBe(1);
  });

  it('excludes null, undefined, and empty-string values', () => {
    const opts = deriveCountedOptions(items, (i) => i.tag);
    expect(opts).toHaveLength(3);
    expect(opts.some((o) => o.value === '')).toBe(false);
  });

  it('sorts by count desc, then label asc', () => {
    const opts = deriveCountedOptions(items, (i) => i.tag);
    expect(opts.map((o) => o.value)).toEqual(['Lipids', 'Glucose', 'Hormones']);
  });

  it('resolves ties alphabetically', () => {
    const tied = [
      { tag: 'Zebra' },
      { tag: 'Apple' },
      { tag: 'Zebra' },
      { tag: 'Apple' },
    ];
    const opts = deriveCountedOptions(tied, (i) => i.tag);
    expect(opts.map((o) => o.value)).toEqual(['Apple', 'Zebra']);
  });

  it('uses labelFn to transform the displayed label', () => {
    const opts = deriveCountedOptions([{ tag: 'loinc' }], (i) => i.tag, (v) => v.toUpperCase());
    expect(opts[0].label).toBe('LOINC');
    expect(opts[0].value).toBe('loinc');
  });

  it('returns an empty array for empty input', () => {
    expect(deriveCountedOptions([], (i) => (i as any).tag)).toEqual([]);
  });
});
