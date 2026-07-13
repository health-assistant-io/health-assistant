import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFilterState, serializeFilterState, parseFilterState } from '../useFilterState';
import type { FacetDefinition } from '../types';

interface Item {
  name: string;
  category: string;
  active: boolean;
  score: number;
}

const items: Item[] = [
  { name: 'a', category: 'Lipids', active: true, score: 5 },
  { name: 'b', category: 'Lipids', active: false, score: 10 },
  { name: 'c', category: 'Glucose', active: true, score: 20 },
];

const categoryFacet: FacetDefinition<Item> = {
  id: 'category',
  label: 'Category',
  kind: 'multi',
  mode: 'client',
  predicate: (item, value) => value.kind === 'multi' && value.values.includes(item.category),
};

const activeFacet: FacetDefinition<Item> = {
  id: 'active',
  label: 'Active only',
  kind: 'toggle',
  mode: 'client',
  predicate: (item, value) => value.kind !== 'toggle' || !value.on || item.active,
};

const rangeFacet: FacetDefinition<Item> = {
  id: 'score',
  label: 'Score',
  kind: 'range',
  mode: 'client',
  predicate: (item, value) => {
    if (value.kind !== 'range') return true;
    if (value.min !== null && item.score < value.min) return false;
    if (value.max !== null && item.score > value.max) return false;
    return true;
  },
};

const serverRegionFacet: FacetDefinition<Item> = {
  id: 'region',
  label: 'Region',
  kind: 'single',
  mode: 'server',
  serverParam: 'region',
  serverValueSerializer: (v) => (v.kind === 'single' && v.value ? v.value : undefined),
};

const urlStatusFacet: FacetDefinition<Item> = {
  id: 'status',
  label: 'Status',
  kind: 'single',
  mode: 'client',
  syncToUrl: true,
  predicate: (item, value) => value.kind !== 'single' || !value.value || item.category === value.value,
};

const allFacets: FacetDefinition<Item>[] = [
  categoryFacet,
  activeFacet,
  rangeFacet,
  serverRegionFacet,
  urlStatusFacet,
];

describe('useFilterState', () => {
  describe('initial state', () => {
    it('starts with all facets at their default values', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      const { state } = result.current;
      expect(state.category).toEqual({ kind: 'multi', values: [] });
      expect(state.active).toEqual({ kind: 'toggle', on: false });
      expect(state.score).toEqual({ kind: 'range', min: null, max: null });
      expect(state.region).toEqual({ kind: 'single', value: null });
      expect(state.status).toEqual({ kind: 'single', value: null });
    });

    it('reports isActive=false and activeCount=0 when nothing is set', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      expect(result.current.isActive).toBe(false);
      expect(result.current.activeCount).toBe(0);
    });

    it('merges provided initialState over defaults', () => {
      const { result } = renderHook(() =>
        useFilterState(allFacets, {
          initialState: { category: { kind: 'multi', values: ['Lipids'] } },
        }),
      );
      expect(result.current.state.category).toEqual({ kind: 'multi', values: ['Lipids'] });
      expect(result.current.isActive).toBe(true);
      expect(result.current.activeCount).toBe(1);
    });
  });

  describe('set', () => {
    it('updates a facet value', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.set('active', { kind: 'toggle', on: true }));
      expect(result.current.state.active).toEqual({ kind: 'toggle', on: true });
      expect(result.current.isActive).toBe(true);
      expect(result.current.activeCount).toBe(1);
    });
  });

  describe('toggle (multi)', () => {
    it('adds an option when absent', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      expect(result.current.state.category).toEqual({ kind: 'multi', values: ['Lipids'] });
    });

    it('appends a second option keeping order', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.toggle('category', 'Glucose'));
      expect(result.current.state.category).toEqual({ kind: 'multi', values: ['Lipids', 'Glucose'] });
    });

    it('removes an option when present', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.toggle('category', 'Glucose'));
      act(() => result.current.toggle('category', 'Lipids'));
      expect(result.current.state.category).toEqual({ kind: 'multi', values: ['Glucose'] });
    });
  });

  describe('toggle (single)', () => {
    it('sets the value when different', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('status', 'Lipids'));
      expect(result.current.state.status).toEqual({ kind: 'single', value: 'Lipids' });
    });

    it('clears the value when toggling the already-selected option', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('status', 'Lipids'));
      act(() => result.current.toggle('status', 'Lipids'));
      expect(result.current.state.status).toEqual({ kind: 'single', value: null });
    });
  });

  describe('clear', () => {
    it('resets a single facet to its default', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.set('active', { kind: 'toggle', on: true }));
      act(() => result.current.clear('category'));
      expect(result.current.state.category).toEqual({ kind: 'multi', values: [] });
      expect(result.current.state.active).toEqual({ kind: 'toggle', on: true });
      expect(result.current.activeCount).toBe(1);
    });
  });

  describe('clearAll', () => {
    it('resets every facet to default', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.set('active', { kind: 'toggle', on: true }));
      act(() => result.current.clearAll());
      expect(result.current.activeCount).toBe(0);
      expect(result.current.isActive).toBe(false);
    });
  });

  describe('applyFilters', () => {
    it('returns all items unchanged when no facet is active', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      expect(result.current.applyFilters(items)).toEqual(items);
    });

    it('runs active client predicates and excludes non-matching items', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      const filtered = result.current.applyFilters(items);
      expect(filtered.map((i) => i.name)).toEqual(['a', 'b']);
    });

    it('combines multiple facets with AND semantics', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.set('active', { kind: 'toggle', on: true }));
      const filtered = result.current.applyFilters(items);
      expect(filtered.map((i) => i.name)).toEqual(['a']);
    });

    it('respects range facets', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.set('score', { kind: 'range', min: 8, max: null }));
      expect(result.current.applyFilters(items).map((i) => i.name)).toEqual(['b', 'c']);
    });

    it('does NOT apply server-mode facets client-side', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.set('region', { kind: 'single', value: 'EU' }));
      expect(result.current.applyFilters(items)).toEqual(items);
    });
  });

  describe('matches', () => {
    it('returns true for every item when no facet is active', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      expect(items.every((i) => result.current.matches(i))).toBe(true);
    });

    it('returns false for items excluded by an active client facet', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      expect(result.current.matches(items[0])).toBe(true);
      expect(result.current.matches(items[2])).toBe(false);
    });

    it('ignores server-mode facets (matches only applies client predicates)', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.set('region', { kind: 'single', value: 'EU' }));
      expect(items.every((i) => result.current.matches(i))).toBe(true);
    });
  });

  describe('serverParams', () => {
    it('serializes only active server-mode facets', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.set('region', { kind: 'single', value: 'EU' }));
      expect(result.current.serverParams).toEqual({ region: 'EU' });
    });

    it('omits server facets at default value', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      expect(result.current.serverParams).toEqual({});
    });

    it('joins array serializer output with commas', () => {
      const multiServer: FacetDefinition<Item> = {
        id: 'tags',
        label: 'Tags',
        kind: 'multi',
        mode: 'server',
        serverParam: 'tags',
        serverValueSerializer: (v) => (v.kind === 'multi' ? v.values : undefined),
      };
      const { result } = renderHook(() => useFilterState([multiServer]));
      act(() => result.current.toggle('tags', 'x'));
      act(() => result.current.toggle('tags', 'y'));
      expect(result.current.serverParams).toEqual({ tags: 'x,y' });
    });
  });

  describe('serialize / parse (URL sync)', () => {
    it('serialize emits only syncToUrl facets with non-default values', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.toggle('status', 'Glucose'));
      expect(result.current.serialize()).toEqual({ status: 'Glucose' });
    });

    it('serialize emits multi values comma-joined', () => {
      const multiUrl: FacetDefinition<Item> = {
        id: 'cats',
        label: 'Cats',
        kind: 'multi',
        mode: 'client',
        syncToUrl: true,
      };
      const { result } = renderHook(() => useFilterState([multiUrl]));
      act(() => result.current.toggle('cats', 'x'));
      act(() => result.current.toggle('cats', 'y'));
      expect(result.current.serialize()).toEqual({ cats: 'x,y' });
    });

    it('serialize emits toggle as "1"', () => {
      const toggleUrl: FacetDefinition<Item> = {
        id: 'flag',
        label: 'Flag',
        kind: 'toggle',
        mode: 'client',
        syncToUrl: true,
      };
      const { result } = renderHook(() => useFilterState([toggleUrl]));
      act(() => result.current.set('flag', { kind: 'toggle', on: true }));
      expect(result.current.serialize()).toEqual({ flag: '1' });
    });

    it('parse round-trips a serialized params record back into FilterState', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      const params = result.current.serialize();
      // Re-parse into a fresh hook with the same facets.
      const { result: result2 } = renderHook(() =>
        useFilterState(allFacets, { initialState: result.current.parse(params) }),
      );
      expect(result2.current.state).toEqual(result.current.state);
    });

    it('parse reconstructs multi values from a comma-joined string', () => {
      const multiUrl: FacetDefinition<Item> = {
        id: 'cats',
        label: 'Cats',
        kind: 'multi',
        mode: 'client',
        syncToUrl: true,
      };
      const { result } = renderHook(() => useFilterState([multiUrl]));
      const parsed = result.current.parse({ cats: 'x,y' });
      expect(parsed.cats).toEqual({ kind: 'multi', values: ['x', 'y'] });
    });

    it('parse ignores params for facets without syncToUrl', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      const parsed = result.current.parse({ category: 'Lipids', status: 'Glucose' });
      expect(parsed.category).toBeUndefined();
      expect(parsed.status).toEqual({ kind: 'single', value: 'Glucose' });
    });
  });

  describe('serializeFilterState / parseFilterState (standalone)', () => {
    it('serializes all non-default facets regardless of syncToUrl', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.set('active', { kind: 'toggle', on: true }));
      const str = serializeFilterState(allFacets, result.current.state);
      const parsed = JSON.parse(str);
      expect(parsed.category).toBe('Lipids');
      expect(parsed.active).toBe('1');
    });

    it('returns empty string when nothing is active', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      expect(serializeFilterState(allFacets, result.current.state)).toBe('');
    });

    it('round-trips through parseFilterState', () => {
      const { result } = renderHook(() => useFilterState(allFacets));
      act(() => result.current.toggle('category', 'Lipids'));
      act(() => result.current.toggle('category', 'Glucose'));
      act(() => result.current.set('active', { kind: 'toggle', on: true }));
      const str = serializeFilterState(allFacets, result.current.state);
      const parsed = parseFilterState(allFacets, str);
      expect(parsed.category).toEqual({ kind: 'multi', values: ['Lipids', 'Glucose'] });
      expect(parsed.active).toEqual({ kind: 'toggle', on: true });
    });

    it('drops unknown keys gracefully', () => {
      const parsed = parseFilterState(allFacets, '{"nonexistent":"foo","status":"High"}');
      expect(parsed.nonexistent).toBeUndefined();
      expect(parsed.status).toEqual({ kind: 'single', value: 'High' });
    });

    it('returns empty object for invalid JSON', () => {
      expect(parseFilterState(allFacets, 'not json')).toEqual({});
    });

    it('returns empty object for empty string', () => {
      expect(parseFilterState(allFacets, '')).toEqual({});
    });
  });

  describe('storageKey (localStorage persistence)', () => {
    beforeEach(() => {
      window.localStorage.clear();
    });

    it('loads initial state from localStorage', () => {
      const str = serializeFilterState(allFacets, {
        category: { kind: 'multi', values: ['Lipids'] },
        active: { kind: 'toggle', on: false },
        score: { kind: 'range', min: null, max: null },
        region: { kind: 'single', value: null },
        status: { kind: 'single', value: null },
      });
      window.localStorage.setItem('test-filters', str);
      const { result } = renderHook(() =>
        useFilterState(allFacets, { storageKey: 'test-filters' }),
      );
      expect(result.current.state.category).toEqual({ kind: 'multi', values: ['Lipids'] });
      expect(result.current.isActive).toBe(true);
    });

    it('saves state to localStorage on change', () => {
      const { result } = renderHook(() =>
        useFilterState(allFacets, { storageKey: 'test-filters' }),
      );
      act(() => result.current.toggle('category', 'Lipids'));
      const stored = window.localStorage.getItem('test-filters');
      expect(stored).not.toBeNull();
      const parsed = JSON.parse(stored!);
      expect(parsed.category).toBe('Lipids');
    });

    it('removes localStorage entry when cleared', () => {
      const { result } = renderHook(() =>
        useFilterState(allFacets, { storageKey: 'test-filters' }),
      );
      act(() => result.current.toggle('category', 'Lipids'));
      expect(window.localStorage.getItem('test-filters')).not.toBeNull();
      act(() => result.current.clearAll());
      expect(window.localStorage.getItem('test-filters')).toBeNull();
    });
  });
});
