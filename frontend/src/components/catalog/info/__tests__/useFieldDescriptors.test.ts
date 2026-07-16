import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useFieldDescriptors } from '../useFieldDescriptors';
import type { CatalogItem } from '../../../../types/catalog';

describe('useFieldDescriptors', () => {
  it('returns no sections/unknowns when item is null', () => {
    const { result } = renderHook(() => useFieldDescriptors('biomarker', null));
    expect(result.current.sections).toEqual([]);
    expect(result.current.unknowns).toEqual([]);
  });

  it('groups biomarker fields into named sections and buckets leftovers', () => {
    const item: CatalogItem = {
      name: 'Glucose',
      slug: 'glucose',
      code: '2345-7',
      coding_system: 'loinc',
      aliases: ['FBS', 'Fasting glucose'],
      info: 'Blood sugar level.',
      is_telemetry: false,
      meta_data: null,
      preferred_unit_symbol: 'mg/dL',
      // leftover not in the registry
      custom_extra: 'something',
    } as CatalogItem;
    const { result } = renderHook(() => useFieldDescriptors('biomarker', item));

    const sectionIds = result.current.sections.map((s) => s.id);
    expect(sectionIds).toContain('identity');
    expect(sectionIds).toContain('coding');
    expect(sectionIds).toContain('unit');
    expect(sectionIds).toContain('clinical');

    // meta_data is null + hideWhenEmpty:false → still present in a meta section
    // (or could be omitted if the section has no other content); custom_extra → additional
    expect(result.current.unknowns.some((u) => u.key === 'custom_extra')).toBe(true);
  });

  it('hides empty fields by default (hideWhenEmpty)', () => {
    const item = {
      name: 'X',
      code: '', // empty → hidden
      info: '', // empty richtext → hidden
    } as CatalogItem;
    const { result } = renderHook(() => useFieldDescriptors('biomarker', item));
    const allKeys = result.current.sections.map((s) => s.descriptors.map((d) => d.key)).flat();
    expect(allKeys).not.toContain('code');
    expect(allKeys).not.toContain('info');
  });

  it('keeps hideWhenEmpty:false fields even when empty (meta_data)', () => {
    const item = { name: 'C', meta_data: null } as unknown as CatalogItem;
    const { result } = renderHook(() => useFieldDescriptors('concept', item));
    const metaSection = result.current.sections.find((s) => s.id === 'meta');
    expect(metaSection?.descriptors.some((d) => d.key === 'meta_data')).toBe(true);
  });

  it('never exposes META_KEYS fields (id, scope, is_custom, …)', () => {
    const item = {
      name: 'C',
      id: 'abc',
      scope: 'system',
      is_custom: false,
      created_at: '2026-01-01',
      weird: 'ok',
    } as unknown as CatalogItem;
    const { result } = renderHook(() => useFieldDescriptors('concept', item));
    const allKeys = [
      ...result.current.sections.map((s) => s.descriptors.map((d) => d.key)).flat(),
      ...result.current.unknowns.map((u) => u.key),
    ];
    expect(allKeys).not.toContain('id');
    expect(allKeys).not.toContain('scope');
    expect(allKeys).not.toContain('is_custom');
    expect(allKeys).not.toContain('created_at');
    // non-meta leftover still surfaces
    expect(allKeys).toContain('weird');
  });

  it('falls back gracefully when catalogType is undefined (no named sections; all non-meta keys → unknowns)', () => {
    const item = { name: 'C', slug: 'c', odd: 1 } as unknown as CatalogItem;
    const { result } = renderHook(() => useFieldDescriptors(undefined, item));
    // No registry applies → no named sections (the orchestrator builds the
    // "additional" section from `unknowns`).
    expect(result.current.sections).toEqual([]);
    expect(result.current.unknowns.map((u) => u.key).sort()).toEqual(['name', 'odd', 'slug']);
  });
});
