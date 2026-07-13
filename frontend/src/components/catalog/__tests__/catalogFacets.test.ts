import { describe, it, expect } from 'vitest';
import type { CatalogItem } from '../../../types/catalog';
import {
  allergyCategoryFacet,
  allergyCustomFacet,
  vaccineCodingSystemFacet,
  medicationCustomFacet,
  conceptStatusFacet,
  catalogAllergyFacets,
  catalogVaccineFacets,
  catalogMedicationFacets,
  catalogConceptFacets,
} from '../catalogFacets';

const multi = (values: string[]): any => ({ kind: 'multi', values });
const toggle = (on: boolean): any => ({ kind: 'toggle', on });

function item(fields: Record<string, unknown>): CatalogItem {
  return { id: 'x', name: 'X', ...fields } as CatalogItem;
}

const allergyItems: CatalogItem[] = [
  item({ category: 'FOOD', is_custom: false }),
  item({ category: 'FOOD', is_custom: true }),
  item({ category: 'MEDICATION', is_custom: false }),
  item({ is_custom: true }),
];

describe('allergyCategoryFacet', () => {
  it('derives counted options excluding items without a category', () => {
    const opts = allergyCategoryFacet.getOptions!(allergyItems);
    expect(opts.map((o) => o.value)).toEqual(['FOOD', 'MEDICATION']);
    expect(opts.find((o) => o.value === 'FOOD')?.count).toBe(2);
  });

  it('matches items by category and excludes uncategorized when active', () => {
    expect(allergyCategoryFacet.predicate!(allergyItems[0], multi(['FOOD']))).toBe(true);
    expect(allergyCategoryFacet.predicate!(allergyItems[2], multi(['FOOD']))).toBe(false);
    expect(allergyCategoryFacet.predicate!(allergyItems[3], multi(['FOOD']))).toBe(false);
  });
});

describe('allergyCustomFacet', () => {
  it('keeps only custom items when toggle is on', () => {
    expect(allergyCustomFacet.predicate!(allergyItems[1], toggle(true))).toBe(true);
    expect(allergyCustomFacet.predicate!(allergyItems[0], toggle(true))).toBe(false);
  });
});

describe('vaccineCodingSystemFacet', () => {
  const items: CatalogItem[] = [
    item({ coding_system: 'cvx' }),
    item({ coding_system: 'cvx' }),
    item({ coding_system: 'snomed' }),
  ];

  it('uppercases option labels', () => {
    const opts = vaccineCodingSystemFacet.getOptions!(items);
    expect(opts.map((o) => o.label)).toEqual(['CVX', 'SNOMED']);
  });

  it('matches by the raw value', () => {
    expect(vaccineCodingSystemFacet.predicate!(items[0], multi(['cvx']))).toBe(true);
    expect(vaccineCodingSystemFacet.predicate!(items[2], multi(['cvx']))).toBe(false);
  });
});

describe('medicationCustomFacet', () => {
  it('keeps only custom items when toggle is on', () => {
    expect(medicationCustomFacet.predicate!(item({ is_custom: true }), toggle(true))).toBe(true);
    expect(medicationCustomFacet.predicate!(item({ is_custom: false }), toggle(true))).toBe(false);
  });
});

describe('conceptStatusFacet', () => {
  const items: CatalogItem[] = [
    item({ status: 'draft' }),
    item({ status: 'active' }),
    item({ status: 'active' }),
    item({ status: 'retired' }),
  ];

  it('capitalizes option labels', () => {
    const opts = conceptStatusFacet.getOptions!(items);
    expect(opts.map((o) => o.label)).toEqual(['Active', 'Draft', 'Retired']);
  });

  it('matches by raw status value', () => {
    expect(conceptStatusFacet.predicate!(items[0], multi(['draft']))).toBe(true);
    expect(conceptStatusFacet.predicate!(items[1], multi(['draft']))).toBe(false);
  });
});

describe('facet arrays', () => {
  it('allergy has category + is_custom in order', () => {
    expect(catalogAllergyFacets.map((f) => f.id)).toEqual(['category', 'is_custom']);
  });
  it('vaccine has coding_system', () => {
    expect(catalogVaccineFacets.map((f) => f.id)).toEqual(['coding_system']);
  });
  it('medication has is_custom', () => {
    expect(catalogMedicationFacets.map((f) => f.id)).toEqual(['is_custom']);
  });
  it('concept has status', () => {
    expect(catalogConceptFacets.map((f) => f.id)).toEqual(['status']);
  });
  it('every facet is client-mode', () => {
    for (const f of [...catalogAllergyFacets, ...catalogVaccineFacets, ...catalogMedicationFacets, ...catalogConceptFacets]) {
      expect(f.mode).toBe('client');
    }
  });
});
