import { describe, it, expect } from 'vitest';
import { DEFAULT_CREATE_ITEMS } from '../CreateMenu';
import en from '../../../locales/en/common.json';
import el from '../../../locales/el/common.json';

type Node = Record<string, unknown>;

const lookup = (root: Node, path: string): unknown =>
  path.split('.').reduce<unknown>((acc, key) => {
    if (acc && typeof acc === 'object') return (acc as Node)[key];
    return undefined;
  }, root);

describe('DEFAULT_CREATE_ITEMS', () => {
  it('routes New examination to the list create intent (not the upload page)', () => {
    const item = DEFAULT_CREATE_ITEMS.find((entry) => entry.id === 'examination');
    expect(item?.to).toBe('/examinations?new=examination');
    expect(item?.requiresPatient).toBe(true);
  });

  it('keeps the smart/bulk upload page out of the create menu', () => {
    expect(
      DEFAULT_CREATE_ITEMS.some((entry) => entry.to === '/examinations/upload'),
    ).toBe(false);
  });

  it('routes New vaccination to the list create intent', () => {
    const item = DEFAULT_CREATE_ITEMS.find((entry) => entry.id === 'vaccination');
    expect(item?.to).toBe('/vaccinations?new=vaccination');
    expect(item?.requiresPatient).toBe(true);
    expect(item?.category).toBe('clinical');
  });

  it('keeps every create label resolvable in both locales', () => {
    for (const entry of DEFAULT_CREATE_ITEMS) {
      expect(lookup(en as Node, entry.labelKey), `en:${entry.labelKey}`).toEqual(
        expect.any(String),
      );
      expect(lookup(el as Node, entry.labelKey), `el:${entry.labelKey}`).toEqual(
        expect.any(String),
      );
    }
  });
});
