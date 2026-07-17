import { describe, it, expect } from 'vitest';
import { clientFilter, normalizeResult, stringFacet } from '../_shared';
import type { InstanceQuery } from '../../../../components/instances/types';

interface Item {
  name: string;
  tag?: string;
}

const items: Item[] = [
  { name: 'Alpha', tag: 'lab' },
  { name: 'Beta', tag: 'lab' },
  { name: 'Gamma', tag: 'imaging' },
];

const baseQuery = (q?: string): InstanceQuery => ({
  q,
  limit: 50,
  offset: 0,
  serverParams: {},
});

describe('adapter _shared helpers', () => {
  it('clientFilter is a no-op when q is empty', () => {
    expect(clientFilter(items, '', (i) => [i.name])).toEqual(items);
    expect(clientFilter(items, undefined, (i) => [i.name])).toEqual(items);
  });

  it('clientFilter matches case-insensitively across fields', () => {
    expect(clientFilter(items, 'alp', (i) => [i.name])).toEqual([items[0]]);
    expect(clientFilter(items, 'LAB', (i) => [i.tag])).toEqual([items[0], items[1]]);
  });

  it('normalizeResult filters and sets total to filtered length', () => {
    const res = normalizeResult(items, baseQuery('lab'), (i) => [i.tag]);
    expect(res.items).toHaveLength(2);
    expect(res.total).toBe(2);
  });

  it('stringFacet derives counted options sorted by count desc', () => {
    const facet = stringFacet<Item>('tag', 'Tag', (i) => i.tag);
    const opts = facet.getOptions!(items);
    expect(opts.find((o) => o.value === 'lab')?.count).toBe(2);
    expect(opts.find((o) => o.value === 'imaging')?.count).toBe(1);
    // lab (2) ranks above imaging (1)
    expect(opts[0].value).toBe('lab');
  });

  it('stringFacet predicate filters by membership', () => {
    const facet = stringFacet<Item>('tag', 'Tag', (i) => i.tag);
    const multi = { kind: 'multi' as const, values: ['lab'] };
    expect(items.filter((i) => facet.predicate!(i, multi))).toEqual([
      items[0],
      items[1],
    ]);
  });
});
