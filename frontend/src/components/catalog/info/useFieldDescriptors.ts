/**
 * useFieldDescriptors — groups a catalog item's fields into ordered sections
 * per the {@link TYPE_FIELDS} registry, and collects any leftover keys into an
 * "additional" bucket.
 *
 * Pure data shaping (no rendering, no i18n): the orchestrator resolves labels
 * and renders. Sections appear in {@link SECTION_ORDER}; only sections with at
 * least one non-empty field are returned. Fields with `hideWhenEmpty` (default
 * true) are dropped when their value is null/empty/[] — set `hideWhenEmpty:
 * false` to always show (e.g. metadata, stratified ranges).
 */
import { useMemo } from 'react';
import type { CatalogItem } from '../../../types/catalog';
import type { CatalogType } from '../../../types/catalog';
import {
  TYPE_FIELDS,
  SECTION_ORDER,
  META_KEYS,
  type FieldDescriptor,
  type SectionId,
} from './fieldRegistry';

export interface SectionGroup {
  id: SectionId;
  /** Ordered field descriptors (registry order preserved). The orchestrator
   *  renders each via its `kind`; consecutive kv fields keep divide-y continuity. */
  descriptors: FieldDescriptor[];
}

export interface UnknownField {
  key: string;
  value: unknown;
}

export interface FieldDescriptorsResult {
  sections: SectionGroup[];
  unknowns: UnknownField[];
}

/** Whether a raw value should be treated as "empty" for display. */
function isEmptyValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') {
    // An empty plain object counts as empty; objects with keys do not.
    return Object.keys(value as object).length === 0;
  }
  return false;
}

export function useFieldDescriptors(
  type: CatalogType | undefined,
  item: CatalogItem | null,
): FieldDescriptorsResult {
  return useMemo(() => {
    if (!item) {
      return { sections: [], unknowns: [] };
    }

    const descriptors = type ? TYPE_FIELDS[type] ?? [] : [];
    const knownKeys = new Set<string>([...META_KEYS]);
    for (const d of descriptors) {
      knownKeys.add(d.key);
      // A `code` descriptor reads its system from a sibling key — that sibling
      // must not also surface as an "additional" leftover (CodeBadge shows it).
      if (d.kind === 'code' && d.systemKey) knownKeys.add(d.systemKey);
      // Likewise an `icon` descriptor may tint from a sibling `colorKey`.
      if (d.kind === 'icon' && d.colorKey) knownKeys.add(d.colorKey);
    }

    // Bucket descriptors by section (preserving registry order), respecting hideWhenEmpty.
    const bySection = new Map<SectionId, { descriptors: FieldDescriptor[] }>();
    for (const d of descriptors) {
      const value = (item as Record<string, unknown>)[d.key];
      const hide = d.hideWhenEmpty !== false; // default true
      if (hide && isEmptyValue(value)) continue;
      let bucket = bySection.get(d.section);
      if (!bucket) {
        bucket = { descriptors: [] };
        bySection.set(d.section, bucket);
      }
      bucket.descriptors.push(d);
    }

    const sections: SectionGroup[] = [];
    for (const id of SECTION_ORDER) {
      const bucket = bySection.get(id);
      if (bucket && bucket.descriptors.length) {
        sections.push({ id, descriptors: bucket.descriptors });
      }
    }

    // Leftover keys → additional fields.
    const unknowns: UnknownField[] = [];
    for (const [key, value] of Object.entries(item)) {
      if (knownKeys.has(key)) continue;
      if (isEmptyValue(value)) continue;
      unknowns.push({ key, value });
    }

    return { sections, unknowns };
  }, [type, item]);
}
