/**
 * CatalogSelection ↔ PickerSelection adapter tests (the multi-select-picker
 * bridge used by CatalogItemPicker).
 *
 * Covers:
 *   1. Round-trip fidelity (type/id/label/relation survive the mapping).
 *   2. Value-key normalization: a pick without a relation shares the key of a
 *      default-relation pick (browse rows match), distinct relations stay
 *      distinct (same item bound two ways).
 *   3. Row-option builders stamp the default relation and keep host data.
 */
import { describe, it, expect } from 'vitest';
import {
  fromPickerSelection,
  hitToOption,
  itemToOption,
  selectionKey,
  toPickerSelection,
} from '../catalogPickerAdapter';
import type { CatalogSelection } from '../../../types/catalog';

const sel = (over: Partial<CatalogSelection> = {}): CatalogSelection => ({
  type: 'medication',
  id: 'm1',
  label: 'Aspirin',
  ...over,
});

describe('catalogPickerAdapter', () => {
  it('round-trips a selection exactly', () => {
    for (const s of [sel(), sel({ relation: 'TREATS' })]) {
      expect(fromPickerSelection(toPickerSelection(s))).toEqual(s);
    }
  });

  it('normalizes the key through the relation default', () => {
    expect(toPickerSelection(sel(), 'AFFECTS').value).toBe(
      toPickerSelection(sel({ relation: 'AFFECTS' }), 'AFFECTS').value,
    );
    expect(toPickerSelection(sel({ relation: 'TREATS' }), 'AFFECTS').value).toBe(
      'medication:m1:TREATS',
    );
    expect(toPickerSelection(sel()).value).toBe('medication:m1');
  });

  it('keeps the relation field round-trip untouched by the normalization', () => {
    expect(fromPickerSelection(toPickerSelection(sel(), 'AFFECTS'))).toEqual(sel());
  });

  it('selectionKey dedups by type:id and relation', () => {
    expect(selectionKey(sel())).toBe('medication:m1');
    expect(selectionKey(sel({ relation: 'X' }))).toBe('medication:m1:X');
  });

  it('hitToOption stamps the default relation and the catalog identity', () => {
    const option = hitToOption(
      { type: 'biomarker', id: 'b9', label: 'CRP' },
      'AFFECTS',
    );
    expect(option).toMatchObject({
      value: 'biomarker:b9:AFFECTS',
      label: 'CRP',
      category: 'biomarker',
      type: 'biomarker',
      id: 'b9',
      relation: 'AFFECTS',
    });
    expect(fromPickerSelection(option)).toEqual({
      type: 'biomarker',
      id: 'b9',
      label: 'CRP',
      relation: 'AFFECTS',
    });
  });

  it('itemToOption keeps the fetched item for row rendering', () => {
    const item = { id: 'a3', name: 'Ankle', scope: 'system' } as never;
    const option = itemToOption(item, 'anatomy', undefined);
    expect(option.value).toBe('anatomy:a3');
    expect(option.item).toBe(item);
    expect(fromPickerSelection(option)).toEqual({
      type: 'anatomy',
      id: 'a3',
      label: 'Ankle',
    });
  });
});
