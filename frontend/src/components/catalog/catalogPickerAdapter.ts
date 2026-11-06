/**
 * CatalogSelection ↔ PickerSelection adapter for the shared
 * `multi-select-picker` module (`components/ui/multiSelectPicker`).
 *
 * The picker works on plain `{value, label, category}` selections; catalog
 * picks need `type` / `id` / `relation` too. Those ride as extra fields on
 * the option objects — the shared module never strips unknown fields, so
 * they round-trip through `value` / `onChange` and back out of the slot
 * callbacks (`renderChipExtra`, `renderRowContent`, `renderBody`).
 *
 * `value` is the dedup key and stays unique per binding: `type:id`, plus
 * `:relation` in relation mode so the same item can be bound two ways. The
 * key is NORMALIZED through the relation default (`relation ??
 * relationPicker.defaultRelation`), so a pick stored without a relation
 * matches a browse row whose option carries the default. The `relation`
 * field itself round-trips untouched.
 */
import type { PickerOption, PickerSelection } from '../ui/multiSelectPicker';
import type { CatalogItem, CatalogSearchHit, CatalogSelection } from '../../types/catalog';

/** Picker option carrying the catalog identity (+ the fetched item when the
 *  browse row came from a per-type listing). */
export interface CatalogPickerOption extends PickerOption {
  type: string;
  id: string;
  relation?: string;
  item?: CatalogItem;
}

/** Dedup key — includes relation so the same item can be bound two ways.
 *  Exported so consumers (e.g. CatalogRelationsEditor) can diff consistently. */
export const selectionKey = (s: CatalogSelection): string =>
  `${s.type}:${s.id}${s.relation ? `:${s.relation}` : ''}`;

const pickerValueFor = (
  type: string,
  id: string,
  label: string,
  relation: string | undefined,
): string => selectionKey({ type, id, label, relation });

export function toPickerSelection(
  sel: CatalogSelection,
  defaultRelation?: string,
): CatalogPickerOption {
  return {
    value: pickerValueFor(sel.type, sel.id, sel.label, sel.relation ?? defaultRelation),
    label: sel.label,
    category: sel.type,
    type: sel.type,
    id: sel.id,
    relation: sel.relation,
  };
}

export function fromPickerSelection(sel: PickerSelection): CatalogSelection {
  const ext = sel as Partial<CatalogPickerOption>;
  if (ext.type !== undefined && ext.id !== undefined) {
    return {
      type: ext.type,
      id: ext.id,
      label: sel.label,
      ...(ext.relation ? { relation: ext.relation } : {}),
    };
  }
  const [type, id, relation] = sel.value.split(':');
  return {
    type,
    id,
    label: sel.label,
    ...(relation ? { relation } : {}),
  };
}

export function hitToOption(
  hit: CatalogSearchHit,
  defaultRelation?: string,
): CatalogPickerOption {
  return {
    value: pickerValueFor(hit.type, hit.id, hit.label, defaultRelation),
    label: hit.label,
    category: hit.type,
    type: hit.type,
    id: hit.id,
    relation: defaultRelation,
  };
}

export function itemToOption(
  item: CatalogItem,
  type: string,
  defaultRelation?: string,
): CatalogPickerOption {
  const id = String(item.id);
  const label = String(item.name ?? item.slug ?? item.id);
  return {
    value: pickerValueFor(type, id, label, defaultRelation),
    label,
    category: type,
    type,
    id,
    relation: defaultRelation,
    item,
  };
}
