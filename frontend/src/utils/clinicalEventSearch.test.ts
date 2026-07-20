import { describe, it, expect, vi } from 'vitest';
import { buildSearchableIndex, searchTypes } from './clinicalEventSearch';
import type { ClinicalEventType } from '../services/clinicalEventService';

/**
 * Minimal fake `t()` for unit tests. Returns the translation when registered,
 * otherwise the fallback (matching i18next's behavior). Tests register the
 * specific keys they care about; unregistered keys degrade to the backend
 * fallback just like production.
 */
function makeFakeT(translations: Record<string, string>) {
  return ((key: string, fallback: string) => {
    return translations[key] ?? fallback;
  }) as any;
}

function makeType(overrides: Partial<ClinicalEventType>): ClinicalEventType {
  return {
    id: 't1',
    name: 'Pregnancy',
    slug: 'pregnancy',
    description: 'Monitor pregnancy milestones, LMP, and estimated due date.',
    schedule_kind: 'state' as any,
    category_concept_id: 'c1',
    category_concept: {
      id: 'c1',
      name: 'Reproductive Health',
      slug: 'reproductive-health',
    },
    ...overrides,
  } as any;
}

describe('buildSearchableIndex', () => {
  it('includes the English backend name + description in the haystack', () => {
    const t = makeFakeT({});
    const index = buildSearchableIndex([makeType({})], t);
    expect(index).toHaveLength(1);
    expect(index[0].haystack).toContain('pregnancy');
    expect(index[0].haystack).toContain('monitor pregnancy milestones');
    expect(index[0].haystack).toContain('lmp');
  });

  it('includes the slug (matches power-user searches)', () => {
    const t = makeFakeT({});
    const index = buildSearchableIndex([makeType({ slug: 'pain-episode' })], t);
    expect(index[0].haystack).toContain('pain-episode');
  });

  it('includes the parent category name + slug', () => {
    const t = makeFakeT({});
    const index = buildSearchableIndex([makeType({})], t);
    expect(index[0].haystack).toContain('reproductive health');
    expect(index[0].haystack).toContain('reproductive-health');
  });

  it('includes localized translations when registered', () => {
    const t = makeFakeT({
      'events.type.pregnancy.name': 'Εγκυμοσύνη',
      'events.type.pregnancy.description': 'Παρακολούθηση εγκυμοσύνης.',
      'events.category.reproductive-health.name': 'Αναπαραγωγική Υγεία',
    });
    const index = buildSearchableIndex([makeType({})], t);
    expect(index[0].haystack).toContain('εγκυμοσύνη');
    expect(index[0].haystack).toContain('παρακολούθηση εγκυμοσύνης');
    expect(index[0].haystack).toContain('αναπαραγωγική υγεία');
  });

  it('falls back to the English string when a translation key is missing', () => {
    // Only the name is translated; description key is absent.
    const t = makeFakeT({ 'events.type.pregnancy.name': 'Εγκυμοσύνη' });
    const index = buildSearchableIndex([makeType({})], t);
    // Localized name present.
    expect(index[0].haystack).toContain('εγκυμοσύνη');
    // English description still present (via fallback).
    expect(index[0].haystack).toContain('monitor pregnancy milestones');
  });

  it('handles a type with no category_concept (defensive)', () => {
    const t = makeFakeT({});
    const index = buildSearchableIndex(
      [makeType({ category_concept: undefined, category_concept_id: '' } as any)],
      t,
    );
    expect(index).toHaveLength(1);
    // Type fields still searchable.
    expect(index[0].haystack).toContain('pregnancy');
  });

  it('lowercases the haystack so search is case-insensitive', () => {
    const t = makeFakeT({});
    const index = buildSearchableIndex([makeType({})], t);
    // Original name was 'Pregnancy' (uppercase P) — haystack should be lowercase.
    expect(index[0].haystack).toBe(index[0].haystack.toLowerCase());
    expect(index[0].haystack).not.toContain('Pregnancy');
  });
});

describe('searchTypes', () => {
  const types = [
    makeType({
      id: 't1',
      name: 'Pregnancy',
      slug: 'pregnancy',
      description: 'Monitor pregnancy milestones.',
    }),
    makeType({
      id: 't2',
      name: 'Pain Episode',
      slug: 'pain-episode',
      description: 'Track chronic pain with intensity.',
      category_concept: { id: 'c2', name: 'Acute & Chronic', slug: 'acute-chronic' },
    }),
    makeType({
      id: 't3',
      name: 'Dental Journey',
      slug: 'dental',
      description: 'Long-term dental treatments.',
      category_concept: { id: 'c3', name: 'Specialized Care', slug: 'specialized-care' },
    }),
  ];

  it('returns an empty list for an empty query', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    expect(searchTypes(index, '')).toEqual([]);
    expect(searchTypes(index, '   ')).toEqual([]);
  });

  it('matches by English type name', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    const out = searchTypes(index, 'pregnancy');
    expect(out.map(t => t.id)).toEqual(['t1']);
  });

  it('matches by English type description', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    const out = searchTypes(index, 'chronic pain');
    expect(out.map(t => t.id)).toEqual(['t2']);
  });

  it('matches by English category name', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    const out = searchTypes(index, 'specialized');
    expect(out.map(t => t.id)).toEqual(['t3']);
  });

  it('matches by slug', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    const out = searchTypes(index, 'pain-episode');
    expect(out.map(t => t.id)).toEqual(['t2']);
  });

  it('matches by localized name when translation is registered', () => {
    const t = makeFakeT({
      'events.type.pregnancy.name': 'Εγκυμοσύνη',
      'events.type.pain-episode.name': 'Επεισόδιο Πόνου',
    });
    const index = buildSearchableIndex(types, t);
    // Greek name search.
    expect(searchTypes(index, 'εγκυμοσύνη').map(t => t.id)).toEqual(['t1']);
    expect(searchTypes(index, 'επεισόδιο').map(t => t.id)).toEqual(['t2']);
  });

  it('BILINGUAL: matches both English AND localized for the same type', () => {
    // The headline Phase 8l behavior — a Greek user can find Pregnancy by
    // typing either "pregnancy" (English source) or "εγκυμοσύνη" (display).
    const t = makeFakeT({
      'events.type.pregnancy.name': 'Εγκυμοσύνη',
    });
    const index = buildSearchableIndex(types, t);
    expect(searchTypes(index, 'pregnancy').map(t => t.id)).toEqual(['t1']);
    expect(searchTypes(index, 'εγκυμοσύνη').map(t => t.id)).toEqual(['t1']);
  });

  it('is case-insensitive', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    expect(searchTypes(index, 'PREGNANCY').map(t => t.id)).toEqual(['t1']);
    expect(searchTypes(index, 'PrEgNaNcY').map(t => t.id)).toEqual(['t1']);
  });

  it('returns multiple matches when the query is broad', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    // "health" matches via category name "Reproductive Health" (Pregnancy)
    // and "Specialized Care" — wait, no "Health" there. Let me pick a query
    // that provably hits 2: both Pregnancy and Pain Episode have "intensity"
    // or "track" in their haystacks via description? Re-check:
    //   Pregnancy: 'Monitor pregnancy milestones.'
    //   Pain: 'Track chronic pain with intensity.'
    //   Dental: 'Long-term dental treatments.'
    // 'with' is only in Pain. 'monitor' is only in Pregnancy. To test
    // multi-match deterministically, craft types that share a word.
    const customTypes = [
      makeType({ id: 'a', description: 'treatment plan a' }),
      makeType({ id: 'b', description: 'treatment plan b' }),
      makeType({ id: 'c', description: 'unrelated' }),
    ];
    const customIndex = buildSearchableIndex(customTypes, makeFakeT({}));
    const out = searchTypes(customIndex, 'treatment');
    expect(out.map(t => t.id).sort()).toEqual(['a', 'b']);
  });

  it('returns empty when nothing matches', () => {
    const index = buildSearchableIndex(types, makeFakeT({}));
    expect(searchTypes(index, 'xyz-not-a-real-query')).toEqual([]);
  });
});
