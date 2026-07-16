import { describe, it, expect } from 'vitest';

import {
  SECTION_META,
  SECTION_ORDER,
  TYPE_FIELDS,
  META_KEYS,
  type SectionId,
} from '../fieldRegistry';
import type { CatalogType } from '../../../../types/catalog';

const ALL_SECTION_IDS: SectionId[] = [
  'identity', 'coding', 'clinical', 'safety', 'reactions', 'schedule',
  'targets', 'presentation', 'aliases', 'display', 'unit', 'reference_ranges',
  'additional', 'meta',
];

describe('fieldRegistry', () => {
  it('SECTION_META has an entry (label + icon) for every SectionId', () => {
    for (const id of ALL_SECTION_IDS) {
      expect(SECTION_META[id], `missing section meta for ${id}`).toBeDefined();
      expect(typeof SECTION_META[id].labelKey).toBe('string');
      expect(SECTION_META[id].icon).toBeDefined();
    }
  });

  it('SECTION_ORDER covers all sections, has no duplicates, and ends with additional', () => {
    expect(new Set(SECTION_ORDER).size).toBe(SECTION_ORDER.length);
    for (const id of ALL_SECTION_IDS) {
      expect(SECTION_ORDER).toContain(id);
    }
    expect(SECTION_ORDER[SECTION_ORDER.length - 1]).toBe('additional');
  });

  it('TYPE_FIELDS has an array for every CatalogType', () => {
    const types: CatalogType[] = ['biomarker', 'medication', 'allergy', 'anatomy', 'vaccine', 'concept'];
    for (const t of types) {
      expect(Array.isArray(TYPE_FIELDS[t]), `${t} must have a descriptor array`).toBe(true);
      expect(TYPE_FIELDS[t].length, `${t} should declare fields`).toBeGreaterThan(0);
    }
  });

  it('every descriptor references a known section', () => {
    for (const [type, descriptors] of Object.entries(TYPE_FIELDS)) {
      for (const d of descriptors) {
        expect(
          ALL_SECTION_IDS.includes(d.section),
          `${type}.${d.key} has unknown section ${d.section}`,
        ).toBe(true);
      }
    }
  });

  it('every declared kind is a known renderer kind; kv descriptors omit it', () => {
    const VALID_KINDS = new Set(['richtext', 'code', 'chips', 'boolean', 'enum', 'refranges', 'dose', 'color', 'icon']);
    for (const [type, descriptors] of Object.entries(TYPE_FIELDS)) {
      for (const d of descriptors) {
        if ('kind' in d && d.kind !== undefined) {
          expect(VALID_KINDS.has(d.kind), `${type}.${d.key} has unknown kind ${d.kind}`).toBe(true);
        }
      }
    }
  });

  it('code descriptors reference a sibling systemKey', () => {
    for (const [type, descriptors] of Object.entries(TYPE_FIELDS)) {
      for (const d of descriptors) {
        if (d.kind === 'code') {
          expect(d.systemKey, `${type}.${d.key} code needs a systemKey`).toBeTruthy();
        }
      }
    }
  });

  it('META_KEYS excludes the audit/scope fields never shown in the body', () => {
    for (const k of ['id', 'tenant_id', 'created_at', 'updated_at', 'scope', 'version', 'is_custom']) {
      expect(META_KEYS.has(k)).toBe(true);
    }
  });

  it('biomarker registry places code under coding and info as richtext', () => {
    const code = TYPE_FIELDS.biomarker.find((d) => d.key === 'code')!;
    expect(code.section).toBe('coding');
    const info = TYPE_FIELDS.biomarker.find((d) => d.key === 'info')!;
    expect(info.kind).toBe('richtext');
  });

  it('concept registry lists kinds + parent_slug under identity', () => {
    const kinds = TYPE_FIELDS.concept.find((d) => d.key === 'kinds')!;
    const parent = TYPE_FIELDS.concept.find((d) => d.key === 'parent_slug')!;
    expect(kinds.section).toBe('identity');
    expect(parent.section).toBe('identity');
  });
});
