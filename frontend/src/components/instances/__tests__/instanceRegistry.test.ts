import { describe, it, expect, beforeEach } from 'vitest';
import {
  registerAdapter,
  getAdapter,
  getAdapters,
  hasAdapter,
  _clearAdaptersForTests,
} from '../instanceRegistry';
import type { InstanceAdapter } from '../types';

function makeAdapter(type: any): InstanceAdapter<any> {
  return {
    type,
    entityLabel: { singular: type, plural: type },
    icon: 'Activity',
    fetch: async () => ({ items: [], total: 0 }),
    fetchOne: async () => ({ id: '1', name: 'x' }),
    facets: [],
    toRow: (item: any) => ({
      id: item.id,
      type,
      label: item.name,
      raw: item,
    }),
    toSelection: (item: any) => ({ type, id: item.id, label: item.name }),
    detailRoute: () => null,
  };
}

describe('instanceRegistry', () => {
  beforeEach(() => {
    _clearAdaptersForTests();
  });

  it('registers and resolves an adapter by type', () => {
    registerAdapter(makeAdapter('examination'));
    expect(hasAdapter('examination')).toBe(true);
    expect(getAdapter('examination').type).toBe('examination');
  });

  it('throws when resolving an unregistered type', () => {
    expect(() => getAdapter('medication')).toThrow(/No adapter registered/);
  });

  it('getAdapters returns all when no filter given', () => {
    registerAdapter(makeAdapter('examination'));
    registerAdapter(makeAdapter('medication'));
    const all = getAdapters();
    expect(all).toHaveLength(2);
    expect(all.map((a) => a.type).sort()).toEqual(['examination', 'medication']);
  });

  it('getAdapters filters to the requested types, preserving order', () => {
    registerAdapter(makeAdapter('examination'));
    registerAdapter(makeAdapter('medication'));
    registerAdapter(makeAdapter('allergy'));
    const subset = getAdapters(['allergy', 'examination']);
    expect(subset.map((a) => a.type)).toEqual(['allergy', 'examination']);
  });

  it('re-registering the same type replaces it', () => {
    registerAdapter(makeAdapter('examination'));
    const next = makeAdapter('examination');
    next.icon = 'Stethoscope';
    registerAdapter(next);
    expect(getAdapter('examination').icon).toBe('Stethoscope');
  });

  it('hasAdapter returns false for unregistered types', () => {
    expect(hasAdapter('vaccine')).toBe(false);
  });
});
